"""The agent loop: plan -> act -> observe, with guards, checkpoints, resume.

Denial codes (normative):
- decide() hard DENY           -> error_code "denied_by_policy", error=reason
- operator denial              -> error_code "denied_by_operator",
                                  error="denied by operator"
- no approver configured       -> error_code "denied_by_operator",
                                  error="no approver configured"
The denied call is NEVER executed; the model sees it in the observation.

v0.1.1 (FIXLIST items 1, 2, 3, 6, 10, 15):
- crash-resume: mid-loop checkpoints carry the live script cursor, and on
  resume a replay ledger is built from the hash-chained log: tool_call
  events after the last completed boundary whose outcome shows they ran
  (or were approved) are "may have executed" — the replayed call gets a
  synthesized observation, is NEVER re-executed and NEVER re-prompted;
  a call whose approval was still pending at crash time is re-prompted
  through the faithful full-payload display (persist requests, never
  verdicts);
- a ``config_loaded`` event with the manifest/policy hashes is appended at
  every start and resume, anchoring WHICH rules the run was judged by;
- provider context errors and the ``max_context_chars`` near-limit estimate
  stop the run in ``stopped_context`` instead of a generic failure loop;
- an approval demand no human can hear (no approver, or a non-tty
  interactive approver) pauses the run in ``stopped_approval`` instead of
  spinning to max_steps;
- approver prompts render the complete request (protocol.render_request);
- observations delimit embedded tool output with a per-session random
  token so model-visible content cannot forge harness framing.
"""

import hashlib
import json
import secrets
import sys
import uuid
from pathlib import Path
from typing import Callable, Protocol, TextIO

from .activity import ActivityLog
from .gate import Gate
from .manifest import Manifest
from .policy import Policy
from .protocol import (format_observation, parse_tool_calls,
                       build_system_prompt, render_request)
from .provider import ModelProvider, ProviderError, ScriptedModelExhausted
from .redact import Redactor
from .state import RunState, activity_path, checkpoint_path, save_checkpoint
from .tools import build_registry
from .tools.base import ToolRegistry
from .types import Action, GateDecision, ToolCall, ToolResult

SYNTHESIZED_MAY_HAVE_EXECUTED = (
    "this call may have executed before the crash; it was not re-executed")


def _call_key(name, args) -> str:
    """Canonical identity of a request — identical to the gate token binding
    (minus the per-process secret), and reconstructible from log events."""
    return name + json.dumps(args, sort_keys=True, separators=(",", ":"))


def _args_sha256(args) -> str:
    """Stable digest of the RAW args (v0.1.2, FIXLIST2 item 1).

    The replay ledger is rebuilt from LOG events, but the log writes args
    through the scrub/truncate pipeline first (activity._scrub: redaction,
    then log_truncate_chars), so a key derived from the logged args cannot
    match a live call whose args are large or contain a credential — the
    already-executed call was re-prompted and re-executed after a crash.
    The digest of the raw args is written ALONGSIDE the (scrubbed) args in
    every tool_call event; it survives scrub/truncate (hex never carries a
    secret verbatim) and never reveals the args (preimage resistance — the
    sanctioned fix shape named by FIXLIST2 item 1)."""
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay_keys(name, args) -> tuple[str, ...]:
    """Every ledger key a live call may match: the raw-args digest first,
    then the legacy logged-args key (v0.1.1 logs have no digest field, and
    for un-truncated, un-redacted args it still matches)."""
    return (_args_sha256(args), _call_key(name, args))


def _is_context_error(text: str) -> bool:
    low = text.lower()
    return ("context length" in low
            or "context_length_exceeded" in low
            or ("context" in low and ("too long" in low or "exceeded" in low)))


class Approver(Protocol):
    def approve(self, call: ToolCall, decision: GateDecision) -> bool: ...


class InteractiveApprover:
    """Prompts a human on a tty. Never blocks on a closed or non-tty stdin.

    Shows the COMPLETE request (protocol.render_request) — the human layer
    decides on what would actually execute, not a 40-char rendering. When no
    interactive terminal is available the denial is marked ``unattended`` so
    the loop can pause cleanly instead of spinning (FIXLIST item 10)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdin
        self.unattended = False

    def approve(self, call: ToolCall, decision: GateDecision) -> bool:
        prompt = f"APPROVE? {render_request(call)} [y/N]: "
        try:
            if not self._stream.isatty():
                self.unattended = True
                print(f"{prompt}n (no interactive terminal)", flush=True)
                return False
            answer = input(prompt)
        except EOFError:
            return False
        return answer.strip().lower() == "y"


class ScriptedApprover:
    """Test approver: pops canned responses and records every prompt — the
    faithful render, exactly what a human would see."""

    def __init__(self, responses: list[bool]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def approve(self, call: ToolCall, decision: GateDecision) -> bool:
        self.prompts.append(render_request(call))
        if not self._responses:
            return False
        return self._responses.pop(0)


class AgentLoop:
    def __init__(self, manifest: Manifest, policy: Policy, provider: ModelProvider,
                 log: ActivityLog, redactor: Redactor,
                 approver: Approver | None = None,
                 fetch: Callable[[str, float], tuple[int, str, bytes]] | None = None,
                 config_hashes: dict[str, str] | None = None,
                 ) -> None:
        self.manifest = manifest
        self.policy = policy
        self.provider = provider
        self.log = log
        self.redactor = redactor
        self.approver = approver
        self.home = Path(manifest.identity.home)
        self.gate = Gate(policy, self.home, log)
        self._config_hashes = config_hashes
        # Per-session random observation delimiter token (FIXLIST item 15):
        # embedded tool output cannot guess or counterfeit the frame.
        self._obs_token = secrets.token_hex(8)
        # Replay ledger (FIXLIST item 1): canonical request key -> True for
        # calls that already ran (or were approved) after the last completed
        # boundary of a crashed run.
        self._replay_skips: dict[str, bool] = {}
        self._replay_active = False
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
            messages=[
                {"role": "system", "content": self._system_prompt},
                # Some OpenAI-compatible endpoints (Z.ai: HTTP 400 code
                # 1214) reject a system-only message list, so the goal
                # opens as a user turn. Reads manifest.goal after the
                # CLI's --goal override has replaced it.
                {"role": "user", "content": f"Goal: {self.manifest.goal}"},
            ],
        )
        # Non-persisted marker: a brand-new state logs run_start even when the
        # caller passes it explicitly (the common `loop.run(loop.new_state())`
        # pattern, e.g. after setting manifest_path / a --goal override).
        state._fresh = True  # noqa: SLF001
        return state

    # -- config anchoring ------------------------------------------------------

    def _log_config_loaded(self) -> None:
        hashes = self._config_hashes or self._fallback_config_hashes()
        self.log.append("config_loaded", manifest_sha256=hashes["manifest_sha256"],
                        policy_sha256=hashes["policy_sha256"])

    def _fallback_config_hashes(self) -> dict[str, str]:
        """Deterministic hashes when the caller (tests, embeddings) did not
        supply the on-disk config bytes."""
        m = self.manifest
        manifest_repr = json.dumps({
            "identity": {"name": m.identity.name, "role": m.identity.role,
                         "home": str(m.identity.home)},
            "goal": m.goal,
            "limits": {"max_steps": m.limits.max_steps,
                       "max_cost_usd": m.limits.max_cost_usd,
                       "max_tool_calls_per_step": m.limits.max_tool_calls_per_step,
                       "max_context_chars": m.limits.max_context_chars},
            "log_truncate_chars": m.log_truncate_chars,
            "provider": {"type": m.provider.type},
        }, sort_keys=True, separators=(",", ":"))
        p = self.policy
        policy_repr = json.dumps({
            "protected_paths": list(p.protected_paths),
            "write_preapproved": list(p.write_preapproved),
            "shell": {"allow": [e if isinstance(e, str) else dict(e)
                                for e in p.shell.allow],
                      "deny": list(p.shell.deny),
                      "timeout_s": p.shell.timeout_s},
            "web": {"allow_domains": list(p.web.allow_domains),
                    "deny_domains": list(p.web.deny_domains),
                    "timeout_s": p.web.timeout_s,
                    "max_bytes": p.web.max_bytes},
        }, sort_keys=True, separators=(",", ":"))
        return {
            "manifest_sha256": hashlib.sha256(manifest_repr.encode()).hexdigest(),
            "policy_sha256": hashlib.sha256(policy_repr.encode()).hexdigest(),
        }

    # -- replay ledger ------------------------------------------------------------

    def _build_replay_ledger(self) -> dict[str, bool]:
        """tool_call events AFTER the last completed boundary (the last
        ``checkpoint`` event) whose outcome proves execution or approval.
        A tool_result, an approval_response(approved) or a gate ALLOW with
        no later pending approval counts; a lone approval_request does not
        (persist requests, never verdicts — the pending call re-prompts).

        Keys (v0.1.2, FIXLIST2 item 1): each entry is keyed by BOTH the
        ``args_sha256`` digest of the RAW args (written alongside the
        scrubbed args in the tool_call event — immune to the scrub/truncate
        the log applies) and, for v0.1.1 logs without the digest, the
        legacy key over the LOGGED args."""
        try:
            entries = list(self.log.entries())
        except Exception:
            return {}
        boundary = 0
        for i, entry in enumerate(entries):
            if entry.get("event") == "checkpoint":
                boundary = i + 1
        ledger: dict[str, bool] = {}
        current_keys: tuple[str, ...] | None = None
        current_tool: str | None = None
        for entry in entries[boundary:]:
            event = entry.get("event")
            tool = entry.get("tool")
            if event == "tool_call":
                digest = entry.get("args_sha256")
                keys = []
                if isinstance(digest, str) and digest:
                    keys.append(digest)
                keys.append(_call_key(tool, entry.get("args", {})))
                current_keys = tuple(keys)
                current_tool = tool
                for key in keys:
                    ledger.setdefault(key, False)
            elif current_keys is not None and tool == current_tool:
                if event == "gate_decision" and entry.get("action") == "allow":
                    for key in current_keys:
                        ledger[key] = True
                elif event == "approval_request":
                    for key in current_keys:
                        ledger[key] = False
                elif event == "approval_response":
                    ran = bool(entry.get("approved"))
                    for key in current_keys:
                        ledger[key] = ran
                elif event == "tool_result":
                    for key in current_keys:
                        ledger[key] = True
        return {key: True for key, ran in ledger.items() if ran}

    # -- the loop --------------------------------------------------------------------

    def run(self, state: RunState | None = None) -> RunState:
        if state is None:
            state = self.new_state()
        if getattr(state, "_fresh", False):
            state._fresh = False  # noqa: SLF001
            self.log.append("run_start", run_id=state.run_id, goal=state.goal)
            self._log_config_loaded()
        else:
            # Guard-stops are resumable (the operator may have raised the
            # limits); terminal outcomes (completed/failed/script_exhausted)
            # and crash-mid-run checkpoints ("running") pass through as-is.
            if state.status.startswith("stopped_"):
                state.status = "running"
            # Resume seed-guard (final review): a checkpoint written before
            # the fix_20 user-seed can hold a [system]-only message list,
            # which Z.ai-class endpoints reject (HTTP 400 code 1214). Seed
            # the same user turn a fresh run gets — from state.goal, the
            # PERSISTED checkpoint goal, never manifest.goal
            # (--allow-config-change can swap the manifest under a resume).
            # Idempotent: post-fix states always carry the user turn.
            if (len(state.messages) == 1
                    and isinstance(state.messages[0], dict)
                    and state.messages[0].get("role") == "system"):
                state.messages.append(
                    {"role": "user", "content": f"Goal: {state.goal}"})
            self.log.append("resume", run_id=state.run_id,
                            steps_done=state.steps_done)
            self._log_config_loaded()
            # Crash-resume double-execution guard (FIXLIST item 1): calls
            # that already ran (or were approved) after the last completed
            # boundary are skipped with a synthesized observation.
            self._replay_skips = self._build_replay_ledger()
            self._replay_active = bool(self._replay_skips)
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
                max_ctx = self.manifest.limits.max_context_chars
                if max_ctx is not None:
                    estimate = sum(
                        len(str(m.get("content", "")))
                        for m in state.messages if isinstance(m, dict))
                    if estimate >= max_ctx:
                        state.status = "stopped_context"
                        self.log.append("guard_trip", kind="context_chars",
                                        limit=max_ctx, value=estimate)
                        break
                try:
                    self.step(state)
                except ProviderError as e:
                    if _is_context_error(str(e)):
                        state.status = "stopped_context"
                        state.failure = self.redactor.scrub(str(e))
                    else:
                        state.status = "failed"
                        state.failure = self.redactor.scrub(str(e))
                    break
                except ScriptedModelExhausted:
                    state.status = "script_exhausted"
                    break
                # The mid-loop checkpoint carries the LIVE script cursor so
                # a crash-resume never replays completed turns (their calls
                # already ran — replaying them re-executes).
                if hasattr(self.provider, "cursor"):
                    state.script_cursor = self.provider.cursor
                save_checkpoint(state, checkpoint_path(self.home), self.redactor)
                self.log.append("checkpoint", n=state.steps_done,
                                status=state.status)
                # The replayed region is closed once its step is durable.
                self._replay_active = False
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
                                               self.manifest.log_truncate_chars,
                                               obs_token=self._obs_token)})
        # 4. Bookkeeping.
        state.steps_done += 1
        self.log.append("step", n=state.steps_done)

    def _act(self, state: RunState, call: ToolCall) -> ToolResult:
        # Replay skip (FIXLIST item 1): this identical request already ran
        # (or was approved) before the crash. Never re-execute, never
        # re-prompt — the model gets the synthesized observation. v0.1.2
        # (FIXLIST2 item 1): the live key is the RAW-args digest first, so
        # args the log truncated or redacted still match their ledger entry.
        if self._replay_active:
            for key in _replay_keys(call.name, call.args):
                if self._replay_skips.get(key):
                    return ToolResult(ok=True, output=SYNTHESIZED_MAY_HAVE_EXECUTED)
        self.log.append("tool_call", tool=call.name, args=call.args,
                        args_sha256=_args_sha256(call.args))
        decision = self.gate.decide(call)
        if decision.action is Action.ALLOW:
            return self.registry.execute(call, decision)
        if decision.action is Action.DENY:
            return ToolResult(ok=False, output="", error=decision.reason,
                              error_code="denied_by_policy")
        self.log.append("approval_request", tool=call.name, args=call.args,
                        reason=decision.reason)
        if self.approver is None:
            self._stop_for_unattended_approval(state, call)
            return ToolResult(ok=False, output="", error="no approver configured",
                              error_code="denied_by_operator")
        approved = self.approver.approve(call, decision)
        if not approved and getattr(self.approver, "unattended", False):
            # A human cannot hear this prompt: pause cleanly instead of
            # spinning to max_steps (FIXLIST item 10).
            self._stop_for_unattended_approval(state, call)
        final = self.gate.resolve_approval(call, approved)
        if final.action is Action.ALLOW:
            return self.registry.execute(call, final)
        return ToolResult(ok=False, output="", error="denied by operator",
                          error_code="denied_by_operator")

    def _stop_for_unattended_approval(self, state: RunState,
                                      call: ToolCall) -> None:
        state.status = "stopped_approval"
        self.log.append("guard_trip", kind="approval_unattended",
                        tool=call.name)


def _new_run_id() -> str:
    return uuid.uuid4().hex
