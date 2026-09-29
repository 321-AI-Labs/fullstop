"""The agent loop: plan -> act -> observe, with guards, checkpoints, resume.

Denial codes (normative):
- decide() hard DENY           -> error_code "denied_by_policy", error=reason
- operator denial              -> error_code "denied_by_operator",
                                  error="denied by operator"
- no approver configured       -> error_code "denied_by_operator",
                                  error="no approver configured"
The denied call is NEVER executed; the model sees it in the observation.
"""

import sys
import uuid
from pathlib import Path
from typing import Callable, Protocol, TextIO

from .activity import ActivityLog
from .gate import Gate
from .manifest import Manifest
from .policy import Policy
from .protocol import format_observation, parse_tool_calls, build_system_prompt, summary
from .provider import ModelProvider, ProviderError, ScriptedModelExhausted
from .redact import Redactor
from .state import RunState, activity_path, checkpoint_path, save_checkpoint
from .tools import build_registry
from .tools.base import ToolRegistry
from .types import Action, GateDecision, ToolCall, ToolResult


class Approver(Protocol):
    def approve(self, call: ToolCall, decision: GateDecision) -> bool: ...


class InteractiveApprover:
    """Prompts a human on a tty. Never blocks on a closed or non-tty stdin."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdin

    def approve(self, call: ToolCall, decision: GateDecision) -> bool:
        prompt = f"APPROVE? {call.name} {summary(call)} [y/N]: "
        try:
            if not self._stream.isatty():
                print(f"{prompt}n (no interactive terminal)", flush=True)
                return False
            answer = input(prompt)
        except EOFError:
            return False
        return answer.strip().lower() == "y"


class ScriptedApprover:
    """Test approver: pops canned responses and records every prompt."""

    def __init__(self, responses: list[bool]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def approve(self, call: ToolCall, decision: GateDecision) -> bool:
        self.prompts.append(f"{call.name} {summary(call)}")
        if not self._responses:
            return False
        return self._responses.pop(0)


class AgentLoop:
    def __init__(self, manifest: Manifest, policy: Policy, provider: ModelProvider,
                 log: ActivityLog, redactor: Redactor,
                 approver: Approver | None = None,
                 fetch: Callable[[str, float], tuple[int, str, bytes]] | None = None
                 ) -> None:
        self.manifest = manifest
        self.policy = policy
        self.provider = provider
        self.log = log
        self.redactor = redactor
        self.approver = approver
        self.home = Path(manifest.identity.home)
        self.gate = Gate(policy, self.home, log)
        scrub_env = tuple(manifest.credential_env_vars)
        if manifest.provider.api_key_env:
            scrub_env += (manifest.provider.api_key_env,)
        self.registry: ToolRegistry = build_registry(
            self.home, policy, redactor, self.gate.verify,
            fetch=fetch, scrub_env=scrub_env)
        self._system_prompt = build_system_prompt(
            manifest, policy, self.registry.schemas())

    def new_state(self) -> RunState:
        state = RunState(
            run_id=_new_run_id(),
            manifest_path="",
            goal=self.manifest.goal,
            status="running",
            messages=[{"role": "system", "content": self._system_prompt}],
        )
        # Non-persisted marker: a brand-new state logs run_start even when the
        # caller passes it explicitly (the common `loop.run(loop.new_state())`
        # pattern, e.g. after setting manifest_path / a --goal override).
        state._fresh = True  # noqa: SLF001
        return state

    def run(self, state: RunState | None = None) -> RunState:
        if state is None:
            state = self.new_state()
        if getattr(state, "_fresh", False):
            state._fresh = False  # noqa: SLF001
            self.log.append("run_start", run_id=state.run_id, goal=state.goal)
        else:
            # Guard-stops are resumable (the operator may have raised the
            # limits); terminal outcomes (completed/failed/script_exhausted)
            # and crash-mid-run checkpoints ("running") pass through as-is.
            if state.status.startswith("stopped_"):
                state.status = "running"
            self.log.append("resume", run_id=state.run_id,
                            steps_done=state.steps_done)
        try:
            while state.status == "running":
                # Guards FIRST.
                if state.steps_done >= self.manifest.limits.max_steps:
                    state.status = "stopped_max_steps"
                    self.log.append("guard_trip", kind="max_steps",
                                    limit=self.manifest.limits.max_steps,
                                    value=state.steps_done)
                    break
                max_cost = self.manifest.limits.max_cost_usd
                if max_cost is not None and state.cost_usd >= max_cost:
                    state.status = "stopped_max_cost"
                    self.log.append("guard_trip", kind="max_cost_usd",
                                    limit=max_cost, value=state.cost_usd)
                    break
                try:
                    self.step(state)
                except ProviderError as e:
                    state.status = "failed"
                    state.failure = self.redactor.scrub(str(e))
                    break
                except ScriptedModelExhausted:
                    state.status = "script_exhausted"
                    break
                save_checkpoint(state, checkpoint_path(self.home), self.redactor)
                self.log.append("checkpoint", n=state.steps_done,
                                status=state.status)
        finally:
            if hasattr(self.provider, "cursor"):
                state.script_cursor = self.provider.cursor
            # Persist the terminal state too, so `status`/`resume` see it.
            save_checkpoint(state, checkpoint_path(self.home), self.redactor)
            self.log.append("run_end", status=state.status,
                            steps_done=state.steps_done)
        return state

    def step(self, state: RunState) -> None:
        # 1. Model turn.
        reply = self.provider.complete(state.messages)
        self.log.append("model_reply", content=reply.content,
                        input_tokens=reply.usage.input_tokens,
                        output_tokens=reply.usage.output_tokens,
                        estimated=reply.usage.estimated)
        state.messages.append({"role": "assistant", "content": reply.content})
        state.tokens_in += reply.usage.input_tokens
        state.tokens_out += reply.usage.output_tokens
        prov = self.manifest.provider
        if prov.usd_per_1k_input is not None and prov.usd_per_1k_output is not None:
            state.cost_usd += reply.usage.cost_usd(prov.usd_per_1k_input,
                                                   prov.usd_per_1k_output)
        # 2. Parse; a clean reply with zero calls ends the run.
        parsed = parse_tool_calls(reply.content,
                                  self.manifest.limits.max_tool_calls_per_step)
        if not parsed:
            state.status = "completed"
            return
        # 3. Execute in order.
        for item in parsed:
            if item.call is None:
                observation = f"ERROR malformed_call: {item.error}"
                state.messages.append({"role": "user", "content": observation})
                continue
            call = item.call
            result = self._act(state, call)
            if result.error_code in ("sandbox_escape", "protected_target"):
                self.log.append("sandbox_block", tool=call.name,
                                detail=result.error or "")
            self.log.append("tool_result", tool=call.name, ok=result.ok,
                            error=result.error,
                            error_code=result.error_code, output=result.output)
            state.messages.append(
                {"role": "user",
                 "content": format_observation(call, result,
                                               self.manifest.log_truncate_chars)})
        # 4. Bookkeeping.
        state.steps_done += 1
        self.log.append("step", n=state.steps_done)

    def _act(self, state: RunState, call: ToolCall) -> ToolResult:
        self.log.append("tool_call", tool=call.name, args=call.args)
        decision = self.gate.decide(call)
        if decision.action is Action.ALLOW:
            return self.registry.execute(call, decision)
        if decision.action is Action.DENY:
            return ToolResult(ok=False, output="", error=decision.reason,
                              error_code="denied_by_policy")
        self.log.append("approval_request", tool=call.name, args=call.args,
                        reason=decision.reason)
        if self.approver is None:
            return ToolResult(ok=False, output="", error="no approver configured",
                              error_code="denied_by_operator")
        approved = self.approver.approve(call, decision)
        final = self.gate.resolve_approval(call, approved)
        if final.action is Action.ALLOW:
            return self.registry.execute(call, final)
        return ToolResult(ok=False, output="", error="denied by operator",
                          error_code="denied_by_operator")


def _new_run_id() -> str:
    return uuid.uuid4().hex
