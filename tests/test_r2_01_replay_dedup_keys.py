"""FIXLIST2 item 1 (medium): replay-dedup key mismatch for large/redacted args.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1.1. The round-1
crash-resume guard rebuilds its replay ledger from LOG events
(fullstop/agent.py:_build_replay_ledger, keys rebuilt from the logged
``tool_call`` args) but keys the LIVE call over the RAW args
(fullstop/agent.py:_act -> _call_key). The log writes args through the
scrub/truncate pipeline FIRST (fullstop/activity.py:_scrub: redaction, then
``log_truncate_chars``), so whenever an executed call's args are

(a) longer than ``log_truncate_chars`` (logged value is truncated), or
(b) containing a value the redactor replaces (logged value is
    ``[REDACTED:NAME]``),

the ledger key differs from the live key: the already-executed AND
already-approved call is not recognized on resume -- it is re-prompted and
re-executed. That is exactly the double execution the round-1 fix promised
to prevent (README claim table; tests/test_fix_01_crash_resume.py).

Frozen contract (FIXLIST2 item 1; either sanctioned fix shape -- deriving
the live key through the same scrub/truncate pipeline, OR storing a hash of
the raw args in the tool_call event -- must satisfy these):

- crash-resume with args LONGER than log_truncate_chars: no re-execution
  (the side effect happens exactly once) and no re-prompt;
- crash-resume with args containing a redacted credential: same two
  guarantees, and the raw credential still never lands in memory.md, the
  activity log, or the checkpoint (credential law; guard, passes today).

The crash uses the same proven subprocess + os._exit(3) fixture as round 1
(tests/test_fix_01_crash_resume.py): a small warmup note completes step 1
(durable mid-loop checkpoint), the large/redacted note executes in step 2,
and the process hard-kills itself right after that tool_result is durable,
so run()'s finally-block never runs. Platform-neutral (subprocess +
os._exit work on Windows and POSIX alike).
"""

import subprocess
import sys
import unittest
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover
from fullstop.policy import Policy
from fullstop.provider import ScriptedModel
from fullstop.redact import Redactor
from fullstop.state import checkpoint_path, load_checkpoint

REPO = support.REPO_ROOT
TESTS = Path(__file__).resolve().parent

WARM = "r2 warmup note"
TRUNCATE = 2000  # must match the driver's ActivityLog truncate_chars
BIG_PREFIX = "R2-BIG-NOTE "
BIG_FILLER = "b" * 2600  # + prefix => longer than TRUNCATE
SECRET = "sk-R2-SECRET-3f9d1c7b5a"
SECRET_TEXT = "token is " + SECRET + " handle with care"

# The driver is a standalone script: it rebuilds the same literals the tests
# use (it cannot import this module).
CRASH_DRIVER = r'''
"""Crash-mid-run driver (FIXLIST2 item 1 test fixture).

Step 1: a small warmup note (completes; mid-loop checkpoint saved).
Step 2: the large/redacted note executes; the process then hard-kills
itself (os._exit(3)) right after that tool_result is durable, so run()'s
finally-block never runs and the on-disk checkpoint stays at step 1 with
status "running". Exit code 3 == the crash happened as scripted.
"""
import os
import sys
from pathlib import Path

tests_dir, home_s, scenario = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, str(tests_dir))
import support
from fullstop.activity import ActivityLog
from fullstop.agent import ScriptedApprover
from fullstop.policy import Policy
from fullstop.redact import Redactor


class CrashingLog(ActivityLog):
    """Delegates to the real log, then os._exit(3) at the chosen event."""

    def __init__(self, path, die_event, die_count, redactor, truncate_chars):
        super().__init__(path, redactor=redactor,
                         truncate_chars=truncate_chars,
                         max_segment_bytes=64_000_000)
        self._die_event = die_event
        self._die_count = die_count
        self._seen = 0

    def append(self, event, **fields):
        out = super().append(event, **fields)
        if event == self._die_event:
            self._seen += 1
            if self._seen >= self._die_count:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(3)
        return out


TRUNCATE = 2000
BIG_PREFIX = "R2-BIG-NOTE "
BIG_FILLER = "b" * 2600
SECRET = "sk-R2-SECRET-3f9d1c7b5a"
WARM = "r2 warmup note"

home = Path(home_s)
redactor = Redactor({})
second_text = BIG_PREFIX + BIG_FILLER
if scenario == "oversize":
    pass
elif scenario == "redacted":
    redactor = Redactor({"R2TOKEN": SECRET})
    second_text = "token is " + SECRET + " handle with care"
else:
    raise SystemExit(f"unknown scenario {scenario}")

replies = [
    support.call_block("note", {"text": WARM}),
    support.call_block("note", {"text": second_text}),
    "done",
]
log = CrashingLog(support.activity_path(home), "tool_result", 2,
                  redactor, TRUNCATE)
loop = support.build_loop(home, replies, policy=Policy(),
                          approver=ScriptedApprover([True, True]),
                          log=log, redactor=redactor)
loop.run(loop.new_state())
raise SystemExit("driver finished without crashing - scenario broken"
                 f" (scenario={scenario})")
'''


class ReplayKeyMismatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    # -- fixture -----------------------------------------------------------

    def _run_crash_driver(self, scenario: str) -> None:
        driver = self.tmp / f"r2_crash_driver_{scenario}.py"
        driver.write_text(CRASH_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(driver), str(TESTS), str(self.home), scenario],
            cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(
            proc.returncode, 3,
            f"crash driver did not crash as scripted (rc={proc.returncode}); "
            f"stdout: {proc.stdout[-2000:]} stderr: {proc.stderr[-2000:]}")

    def _replies(self, second_text: str) -> list[str]:
        return [
            support.call_block("note", {"text": WARM}),
            support.call_block("note", {"text": second_text}),
            "done",
        ]

    def _resume(self, replies: list[str], redactor: Redactor):
        """Resume exactly as the CLI does (script cursor from the durable
        checkpoint). The approver carries one canned approval: the replayed
        call was already approved pre-crash, so if the loop ASKS again the
        canned True makes the defect visible as a re-EXECUTION too."""
        loaded = load_checkpoint(checkpoint_path(self.home))
        provider = ScriptedModel(replies, start_cursor=loaded.script_cursor)
        approver = ScriptedApprover([True])  # must never be asked
        loop = support.build_loop(self.home, replies, policy=Policy(),
                                  approver=approver, provider=provider,
                                  redactor=redactor)
        return loop.run(loaded), approver

    def _memory_lines(self) -> list[str]:
        return (self.home / "memory.md").read_text(encoding="utf-8").splitlines()

    def _log(self):
        from fullstop.activity import ActivityLog
        return ActivityLog(support.activity_path(self.home))

    def _assert_no_approval_after_resume(self) -> None:
        entries = support.read_events(self.home)
        resume_idx = max(i for i, e in enumerate(entries)
                         if e.get("event") == "resume")
        after = entries[resume_idx + 1:]
        self.assertEqual(
            support.events_of(after, "approval_request"), [],
            "approval_request events appended after resume for a call that "
            "was already approved pre-crash")

    # -- scenario fidelity (must keep passing BEFORE and AFTER the fix) ----

    def test_crash_fixture_lands_mid_run_with_transformed_log_args(self):
        """PASSES today - it verifies the scenario, not the fix. The crash
        lands after the second note executed (2 memory lines, durable
        checkpoint still at step 1), and -- the point of this item -- the
        LOGGED args for that call are NOT the raw args: truncated in the
        oversize case, redacted in the credential case. That transformation
        is exactly why the replay ledger's key cannot match the live key."""
        for scenario, second_text in (("oversize", BIG_PREFIX + BIG_FILLER),
                                      ("redacted", SECRET_TEXT)):
            with self.subTest(scenario=scenario):
                home = support.make_home(self.tmp, name=f"home-{scenario}")
                self._run_crash_driver_on(home, scenario)
                state = load_checkpoint(checkpoint_path(home))
                self.assertEqual(state.status, "running")
                self.assertEqual(state.steps_done, 1)
                lines = (home / "memory.md").read_text(
                    encoding="utf-8").splitlines()
                self.assertEqual(len(lines), 2, lines)
                calls = support.events_of(support.read_events(home),
                                          "tool_call")
                self.assertEqual(len(calls), 2)
                logged_text = calls[1]["args"]["text"]
                self.assertNotEqual(
                    logged_text, second_text,
                    "logged tool_call args equal the raw args - the "
                    "key-mismatch premise of this item does not hold")
                if scenario == "oversize":
                    self.assertLess(len(logged_text), len(second_text))
                    self.assertIn("truncated", logged_text)
                else:
                    self.assertIn("[REDACTED:R2TOKEN]", logged_text)
                    self.assertNotIn(SECRET, logged_text)

    def _run_crash_driver_on(self, home: Path, scenario: str) -> None:
        driver = self.tmp / f"r2_crash_driver_{scenario}_{home.name}.py"
        driver.write_text(CRASH_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(driver), str(TESTS), str(home), scenario],
            cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(
            proc.returncode, 3,
            f"crash driver did not crash as scripted (rc={proc.returncode}); "
            f"stdout: {proc.stdout[-2000:]} stderr: {proc.stderr[-2000:]}")

    # -- the fix (all FAIL today) -------------------------------------------

    def test_oversized_args_not_reexecuted_or_reprompted(self):
        """Item 1 case (a): args longer than log_truncate_chars. The call
        already executed and was approved pre-crash; resume must neither
        re-execute it (memory.md keeps exactly two lines, one marker) nor
        re-prompt for it."""
        self._run_crash_driver("oversize")
        second_text = BIG_PREFIX + BIG_FILLER
        state, approver = self._resume(self._replies(second_text), Redactor({}))
        self.assertEqual(state.status, "completed", state.failure)
        lines = self._memory_lines()
        self.assertEqual(
            len(lines), 2,
            f"side effects re-executed across resume (oversized args): "
            f"{[ln[:60] for ln in lines]!r}")
        self.assertEqual(sum(BIG_PREFIX in ln for ln in lines), 1)
        self.assertEqual(
            approver.prompts, [],
            f"resume re-prompted approval for an already-approved call with "
            f"oversized args: {approver.prompts!r}")
        self._assert_no_approval_after_resume()
        ok, first_bad = self._log().verify()
        self.assertEqual((ok, first_bad), (True, None))

    def test_redacted_credential_args_not_reexecuted_or_reprompted(self):
        """Item 1 case (b): args containing a redacted credential. Same two
        guarantees; the memory line legitimately carries the REDACTED marker
        (the note tool scrubs at write), so the count is over the marker."""
        self._run_crash_driver("redacted")
        state, approver = self._resume(self._replies(SECRET_TEXT),
                                       Redactor({"R2TOKEN": SECRET}))
        self.assertEqual(state.status, "completed", state.failure)
        lines = self._memory_lines()
        self.assertEqual(
            len(lines), 2,
            f"side effects re-executed across resume (redacted args): "
            f"{[ln[:60] for ln in lines]!r}")
        self.assertEqual(sum("[REDACTED:R2TOKEN]" in ln for ln in lines), 1)
        self.assertEqual(
            approver.prompts, [],
            f"resume re-prompted approval for an already-approved call with "
            f"redacted args: {approver.prompts!r}")
        self._assert_no_approval_after_resume()
        ok, first_bad = self._log().verify()
        self.assertEqual((ok, first_bad), (True, None))

    def test_raw_secret_never_on_disk_across_crash_and_resume(self):
        """GUARD (passes today; must keep passing): credential law. The raw
        secret appears in model replies and raw args, so every write layer
        (memory.md, activity log, checkpoint) must scrub it -- on the crash
        side and on the resume side alike."""
        self._run_crash_driver("redacted")
        state, _ = self._resume(self._replies(SECRET_TEXT),
                                Redactor({"R2TOKEN": SECRET}))
        self.assertEqual(state.status, "completed", state.failure)
        memory_text = (self.home / "memory.md").read_text(encoding="utf-8")
        log_text = support.activity_path(self.home).read_text(
            encoding="utf-8", errors="replace")
        checkpoint_text = checkpoint_path(self.home).read_text(
            encoding="utf-8", errors="replace")
        for name, text in (("memory.md", memory_text),
                           ("activity log", log_text),
                           ("checkpoint", checkpoint_text)):
            self.assertNotIn(SECRET, text,
                             f"raw credential leaked into {name}")


if __name__ == "__main__":
    unittest.main()
