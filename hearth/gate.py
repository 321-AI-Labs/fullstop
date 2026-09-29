"""The policy gate: pattern classifier + decision table between model and tools.

The gate matches the LITERAL normalized rel. Interior ``..`` may match a glob
(verified: fnmatch('notes/../../escape.txt', 'notes/**') is True) — the sandbox
chokepoint inside the tool is the ordered backstop. Protected READS are
operator-approvable; protected WRITES and denylists are hard denials that
approval can never upgrade.
"""

import hashlib
import json
import secrets
from pathlib import Path

from .activity import ActivityLog
from .policy import Policy
from .types import NOTE_FILENAME, Action, GateDecision, ToolCall


def _rel_of(path_value) -> str:
    """Normative normalization: backslashes->slashes, strip leading ./, casefold."""
    rel = path_value.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.casefold()


class Gate:
    def __init__(self, policy: Policy, workspace: Path,
                 log: ActivityLog | None = None) -> None:
        self._policy = policy
        self.workspace = Path(workspace)
        self._log = log
        self._secret = secrets.token_hex(16)

    # -- classification ------------------------------------------------------

    def classify(self, call: ToolCall) -> str:
        """'consequential' iff write-like, shell, browser, or protected read."""
        if call.name in ("file_write", "note", "shell", "browser"):
            return "consequential"
        if call.name == "file_read":
            path_value = call.args.get("path")
            if isinstance(path_value, str) and self._policy.is_protected(_rel_of(path_value)):
                return "consequential"
        return "read-only"

    # -- decision table --------------------------------------------------------

    def decide(self, call: ToolCall) -> GateDecision:
        decision = self._decide_pure(call)
        self._log_decision(call, decision)
        return decision

    def _decide_pure(self, call: ToolCall) -> GateDecision:
        name = call.name
        args = call.args

        if name == "file_read":
            path_value = args.get("path")
            if not isinstance(path_value, str):
                return GateDecision(Action.DENY, "malformed call: path must be a string")
            rel = _rel_of(path_value)
            if self._policy.is_protected(rel):
                return GateDecision(Action.APPROVAL_REQUIRED,
                                    f"protected path read: {rel}")
            return self._allow(call, "read-only")

        if name == "file_list":
            return self._allow(call, "read-only")

        if name == "note":
            # note is a WRITE gated like file_write against NOTE_FILENAME.
            if self._policy.is_protected(NOTE_FILENAME):
                return GateDecision(Action.DENY, f"protected path: {NOTE_FILENAME}")
            if self._policy.is_write_preapproved(NOTE_FILENAME):
                return self._allow(call, "pre-approved note")
            return GateDecision(Action.APPROVAL_REQUIRED, "note requires approval")

        if name == "file_write":
            path_value = args.get("path")
            if not isinstance(path_value, str):
                return GateDecision(Action.DENY, "malformed call: path must be a string")
            rel = _rel_of(path_value)
            if self._policy.is_protected(rel):
                return GateDecision(Action.DENY, f"protected path: {rel}")
            if self._policy.is_write_preapproved(rel):
                return self._allow(call, "pre-approved write")
            return GateDecision(Action.APPROVAL_REQUIRED, f"consequential write: {rel}")

        if name == "shell":
            argv = args.get("argv")
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(a, str) for a in argv)):
                return GateDecision(Action.DENY, "malformed shell argv")
            if not self._policy.shell.allow:
                return GateDecision(Action.DENY, "shell disabled by policy")
            argv0 = argv[0].casefold()
            deny = {d.casefold() for d in self._policy.shell.deny}
            allow = {a.casefold() for a in self._policy.shell.allow}
            if argv0 in deny:
                return GateDecision(Action.DENY, f"command denied: {argv[0]}")
            if argv0 in allow:
                return self._allow(call, "pre-approved command")
            return GateDecision(Action.APPROVAL_REQUIRED,
                                f"command requires approval: {argv[0]}")

        if name == "web_fetch":
            from urllib.parse import urlsplit
            url = args.get("url")
            if not isinstance(url, str):
                return GateDecision(Action.DENY, "malformed url")
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if parts.scheme.lower() not in ("http", "https") or not host:
                return GateDecision(Action.DENY, "malformed url")
            deny = {d.casefold() for d in self._policy.web.deny_domains}
            allow = {d.casefold() for d in self._policy.web.allow_domains}
            if host in deny:
                return GateDecision(Action.DENY, f"domain denied: {host}")
            if allow and host in allow:
                return self._allow(call, "read-only (allowlisted domain)")
            return GateDecision(Action.APPROVAL_REQUIRED,
                                f"domain requires approval: {host}")

        if name == "browser":
            return GateDecision(Action.APPROVAL_REQUIRED, "browser requires approval")

        return GateDecision(Action.DENY, f"unknown tool: {name}")

    def _allow(self, call: ToolCall, reason: str) -> GateDecision:
        return GateDecision(Action.ALLOW, reason, token=self._mint(call))

    # -- approval ---------------------------------------------------------------

    def resolve_approval(self, call: ToolCall, approved: bool) -> GateDecision:
        current = self._decide_pure(call)
        if approved:
            if current.action is Action.APPROVAL_REQUIRED:
                final = self._allow(call, "approved by operator")
            else:
                # ALLOW stays ALLOW (idempotent); a hard DENY is never upgraded.
                final = current
        else:
            final = GateDecision(Action.DENY, "denied by operator")
        if self._log is not None:
            self._log.append("approval_response", tool=call.name, approved=approved,
                             reason=final.reason)
            self._log_decision(call, final)
        return final

    # -- tokens --------------------------------------------------------------------

    def _mint(self, call: ToolCall) -> str:
        payload = (self._secret + call.name +
                   json.dumps(call.args, sort_keys=True, separators=(",", ":")))
        return hashlib.sha256(payload.encode()).hexdigest()

    def verify(self, call: ToolCall, decision: GateDecision) -> None:
        if decision.action is not Action.ALLOW or decision.token != self._mint(call):
            raise PermissionError(
                f"gate did not mint an ALLOW for {call.name}: refusing to execute")

    # -- logging ---------------------------------------------------------------------

    def _log_decision(self, call: ToolCall, decision: GateDecision) -> None:
        if self._log is not None:
            self._log.append("gate_decision", tool=call.name, args=call.args,
                             action=decision.action.value, reason=decision.reason,
                             classification=self.classify(call))
