"""ShellTool: argv-LIST only, shell=False everywhere, cwd=workspace, timeout.

The empty-allowlist and denylist checks remain as documented hard lines
(denylist is a hard red line the operator cannot approve past, at gate OR tool
level). There is deliberately NO allowlist-membership veto at tool level: an
operator-approved non-allowlisted command EXECUTES; the forged-decision threat
is dead because the registry only passes Gate-minted tokens.
"""

import os
import subprocess
from pathlib import Path

from ..policy import ShellPolicy
from ..redact import Redactor
from ..types import ToolCall, ToolResult
from .base import Tool


def _err(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, output="", error=message, error_code=code)


class ShellTool(Tool):
    name = "shell"

    def __init__(self, policy: ShellPolicy, sandbox_root: Path,
                 redactor: Redactor, scrub_env: tuple[str, ...] = ()) -> None:
        self._policy = policy
        self._root = Path(sandbox_root)
        self._redactor = redactor
        self._scrub_env = tuple(scrub_env)

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": "Run an allowlisted command in the workspace (no shell).",
            "args": {"argv": "list of strings (required), e.g. [\"git\", \"status\"]"},
        }

    def execute(self, call: ToolCall) -> ToolResult:
        argv = call.args.get("argv")
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(a, str) for a in argv)):
            return _err("malformed_call", "shell argv must be a non-empty list of strings")
        argv0 = argv[0].casefold()
        if not self._policy.allow:
            # Unreachable through the gate (the gate hard-denies empty
            # allowlists first); documented default-off posture for direct use.
            return _err("shell_disabled", "shell disabled by policy (empty allowlist)")
        if argv0 in {d.casefold() for d in self._policy.deny}:
            return _err("command_not_allowed", f"command denied: {argv[0]}")
        child_env = {k: v for k, v in os.environ.items()
                     if k not in self._scrub_env}
        try:
            proc = subprocess.run(
                argv, shell=False, cwd=str(self._root), env=child_env,
                capture_output=True, timeout=self._policy.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return _err("timeout",
                        f"command timed out after {self._policy.timeout_s}s")
        output = (f"rc={proc.returncode}\nSTDOUT:\n"
                  f"{proc.stdout.decode('utf-8', errors='replace')}\nSTDERR:\n"
                  f"{proc.stderr.decode('utf-8', errors='replace')}")
        return ToolResult(ok=proc.returncode == 0,
                          output=self._redactor.scrub(output))
