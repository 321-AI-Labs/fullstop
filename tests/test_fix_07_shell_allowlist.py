"""FIXLIST item 7 (must-fix): shell allowlist is argv[0]-only.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 2, ruled on in turn-03 digest): an allowlist entry
pre-approves the PROGRAM regardless of arguments — allowlisting `python`
pre-approves arbitrary code — and on Windows CreateProcess resolves a bare
argv0 against the cwd first, so a planted workspace binary shadows the
intended program (plus the BatBadBut class: a .bat/.cmd argv0 re-parses the
argument string through cmd.exe).

Frozen contract (test-defined schema, minimal and backward-tolerant):

- constrained entries are objects: {"program": str, "args": [pattern, ...]}
  where each pattern matches the corresponding argv element exactly
  (fnmatch; "*" is the narrow wildcard);
- a bare STRING entry stays loadable but pre-approves ONLY the bare
  invocation argv == [program] — no entry can pre-approve arbitrary
  arguments; unmatched arguments always route to approval;
- program matching is on the RESOLVED absolute path (PATH lookup with the
  cwd excluded; absolute-path entries compare full paths) — a same-named
  binary planted in the workspace must not shadow the allowlisted program;
- Windows-only (skipIf-guarded): a .bat/.cmd target is refused outright
  (never executed through cmd.exe re-parsing), with the refusal observable.
"""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import support
from fullstop.policy import policy_from_dict
from fullstop.types import Action, ToolCall

IS_WINDOWS = os.name == "nt"
GIT = shutil.which("git")
WHERE = shutil.which("where.exe") if IS_WINDOWS else None
PY_ABS = sys.executable  # guaranteed-present program, absolute


@unittest.skipUnless(IS_WINDOWS and GIT and WHERE,
                     "needs Windows + git + where.exe on PATH "
                     "(cwd-shadowing scenario is Windows-specific)")
class ShellResolutionWindowsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def test_planted_cwd_binary_does_not_shadow_allowlisted_program(self):
        """A decoy git.exe (a copy of where.exe) sits in the workspace. The
        allowlisted `git --version` must run the PATH git, not the decoy:
        real git prints "git version ..."; where.exe-as-git.exe fails."""
        policy = policy_from_dict({"shell": {
            "allow": [{"program": "git", "args": ["--version"]}]}})
        h = support.gate_harness(self.tmp, policy=policy)
        # plant the decoy INSIDE the workspace (the shell child's cwd)
        shutil.copyfile(WHERE, h.home / "git.exe")
        result = h.execute(ToolCall(
            "shell", {"argv": ["git", "--version"]}))
        self.assertTrue(
            result.ok,
            f"allowlisted git failed to run (decoy shadowing?): {result.error}")
        self.assertIn(
            "git version", result.output,
            f"the workspace-planted binary ran instead of the PATH program; "
            f"output: {result.output[:300]!r}")

    def test_batch_script_target_is_refused_never_executed(self):
        """BatBadBut class (CVE-2024-24576, turn-04 critique 1): a
        .bat/.cmd target must never reach cmd.exe re-parsing, wherever the
        refusal lives (gate hard-deny or tool refusal)."""
        policy = policy_from_dict({"shell": {
            "allow": [{"program": "evil", "args": ["boom"]}]}})
        h = support.gate_harness(self.tmp, policy=policy)
        marker = h.home / "pwned.marker"
        (h.home / "evil.cmd").write_text(
            f'@echo off\r\necho. > "{marker.name}"\r\n', encoding="utf-8")
        call = ToolCall("shell", {"argv": ["evil.cmd", "boom"]})
        decision = h.gate.decide(call)
        if decision.action is not Action.DENY:
            decision = h.gate.resolve_approval(call, True)
        if decision.action is Action.ALLOW:
            result = h.registry.execute(call, decision)
            self.assertFalse(
                result.ok,
                f"a .cmd target executed through the shell tool: {result.output!r}")
        else:
            self.assertIs(decision.action, Action.DENY)
        self.assertFalse(
            marker.exists(),
            "the batch file ran despite the .cmd refusal contract")


class ShellAllowlistContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _gate(self, allow):
        h = support.gate_harness(self.tmp,
                                 policy=policy_from_dict({"shell": {
                                     "allow": allow}}))
        return h.gate

    def test_constrained_entry_exact_args_preapproves(self):
        gate = self._gate([{"program": PY_ABS, "args": ["--version"]}])
        decision = gate.decide(ToolCall(
            "shell", {"argv": [PY_ABS, "--version"]}))
        self.assertEqual(
            decision.action, Action.ALLOW,
            f"exact-args entry must pre-approve exactly this call: "
            f"{decision.reason}")

    def test_unmatched_arguments_always_route_to_approval(self):
        gate = self._gate([{"program": PY_ABS, "args": ["--version"]}])
        for argv in ([PY_ABS, "--version", "--extra"],
                     [PY_ABS, "-c", "import os; os.system('boom')"]):
            with self.subTest(argv=argv[-1][:30]):
                decision = gate.decide(ToolCall("shell", {"argv": argv}))
                self.assertEqual(
                    decision.action, Action.APPROVAL_REQUIRED,
                    f"arguments beyond the entry's constraints must be "
                    f"approved by a human, got {decision.action.value}: "
                    f"{decision.reason}")

    def test_pattern_entry_matches_narrowly(self):
        """Narrow wildcard patterns work positionally: `["-m", "*"]`
        pre-approves `python -m <module>` and nothing else."""
        gate = self._gate([{"program": PY_ABS, "args": ["-m", "*"]}])
        self.assertEqual(
            gate.decide(ToolCall(
                "shell", {"argv": [PY_ABS, "-m", "json.tool"]})).action,
            Action.ALLOW)
        for argv in ([PY_ABS, "-m", "json.tool", "--extra"],
                     [PY_ABS, "-c", "import os"],
                     [PY_ABS, "-m"]):
            with self.subTest(argv=argv[1:]):
                decision = gate.decide(ToolCall("shell", {"argv": argv}))
                self.assertEqual(decision.action, Action.APPROVAL_REQUIRED,
                                 decision.reason)

    def test_bare_string_entry_cannot_preapprove_arguments(self):
        """A bare `python` entry (the shipped example-policy style) must not
        pre-approve `python -c <arbitrary code>`. It may pre-approve only
        the bare invocation with no arguments."""
        gate = self._gate(["python"])
        call = ToolCall("shell", {"argv": ["python", "-c", "import os"]})
        decision = gate.decide(call)
        self.assertEqual(
            decision.action, Action.APPROVAL_REQUIRED,
            f"an argv[0]-only entry pre-approved arbitrary arguments: "
            f"{decision.reason}")

    def test_absolute_path_entry_full_path_comparison(self):
        """Full-path comparison: the same basename from a DIFFERENT path is
        a different program and is not pre-approved."""
        gate = self._gate([{"program": PY_ABS, "args": ["--version"]}])
        fake = "C:/planted/python.exe" if IS_WINDOWS else "/planted/python"
        decision = gate.decide(ToolCall(
            "shell", {"argv": [fake, "--version"]}))
        self.assertEqual(decision.action, Action.APPROVAL_REQUIRED)

    def test_denylisted_program_still_hard_denied(self):
        """GUARD (passes today, must keep passing): deny entries hard-deny."""
        h = support.gate_harness(self.tmp, policy=policy_from_dict(
            {"shell": {"allow": [{"program": PY_ABS, "args": ["--version"]}],
                       "deny": ["rm"]}}))
        decision = h.gate.decide(ToolCall("shell", {"argv": ["rm", "-rf", "/"]}))
        self.assertIs(decision.action, Action.DENY)


if __name__ == "__main__":
    unittest.main()
