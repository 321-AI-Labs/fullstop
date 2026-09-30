"""FIXLIST item 1 (must-fix): crash-resume double execution.

Freeze-tests-before-fixes: these tests are written BEFORE the fix and are
EXPECTED TO FAIL against v0.1. They pin the contract from the deliberation
(DELIB-20260929-devday-fullstop-v01, turn-06-glm "Replay: CONFIRMED" and the
final memo):

- a hard crash mid-step (os._exit, so no finally-block runs) leaves the
  on-disk checkpoint at the last mid-loop save with status "running";
- resume must NEVER re-execute a tool call that already ran (checkpoint
  events mark completed boundaries; tool_call events after the last boundary
  are "may have executed" -> synthesized observation, no execution);
- resume must NEVER re-prompt approval for a call whose identical request was
  already approved (a tool_call with an approval_response after the boundary
  executed or was about to);
- a call whose approval was still PENDING at crash time (approval_request
  logged, no approval_response, no tool_result) did NOT execute and MUST be
  re-prompted (persist requests, never verdicts - turn-04 law) with the
  faithful full-payload display (FIXLIST item 6);
- mid-loop checkpoints must carry the live script cursor so resume does not
  replay completed turns (replaying a completed turn re-executes its calls,
  which is the same defect).

The crash is simulated by a subprocess driver that hard-kills itself with
os._exit(3) at a chosen log event; this exercises the exact crash-mid-run
"running"-checkpoint scenario the README claims to support (memo defect 8).
The scenario itself is platform-neutral (subprocess + os._exit work on
Windows and POSIX alike), so no skipIf guard is needed here; the Windows
skipIf guards the fix list mentions live in the platform-specific shell
tests (test_fix_07_shell_allowlist.py).
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import support
from fullstop.activity import ActivityLog
from fullstop.agent import ScriptedApprover
from fullstop.policy import Policy
from fullstop.provider import ScriptedModel
from fullstop.state import checkpoint_path, load_checkpoint

REPO = support.REPO_ROOT
TESTS = Path(__file__).resolve().parent

NOTE_ONE = "note one"
NOTE_TWO = "note two"
NOTES_REPLIES_MARKER = "all done"
LONG_WRITE = "C" * 250  # long enough to exceed the old 40-char render cap

# The driver is a standalone script: it rebuilds the same literals the tests
# use (it cannot import this module).
CRASH_DRIVER = r'''
"""Crash-mid-run driver (FIXLIST item 1 test fixture).

Runs the loop exactly as the tests do, then hard-kills the process with
os._exit(3) at a chosen log event so run()'s finally-block NEVER runs: the
on-disk checkpoint stays at the last mid-loop save (status "running"), which
is the crash-mid-run scenario. Exit code 3 == the crash happened as
scripted; anything else means the scenario is broken.
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
from fullstop.state import activity_path


class CrashingLog(ActivityLog):
    """Delegates to the real log, then os._exit(3) at the chosen event."""

    def __init__(self, path, die_event, die_count):
        super().__init__(path, redactor=Redactor({}))
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


home = Path(home_s)
if scenario == "notes":
    replies = [
        support.call_block("note", {"text": "note one"}),
        support.call_block("note", {"text": "note two"}),
        "all done",
    ]
    policy = Policy()  # note requires approval; the driver approves both
    approver = ScriptedApprover([True, True])
    # die AFTER the second note executed and its tool_result is durable
    die_event, die_count = "tool_result", 2
elif scenario == "approval":
    (home / "data.txt").write_text("data\n", encoding="utf-8")
    replies = [
        support.call_block("file_read", {"path": "data.txt"}),
        support.call_block("file_write",
                           {"path": "out.md", "content": "C" * 250}),
        "done",
    ]
    policy = Policy()  # file_write requires approval; crash BEFORE approval
    approver = ScriptedApprover([True])  # never reached before the crash
    die_event, die_count = "approval_request", 1
else:
    raise SystemExit(f"unknown scenario {scenario}")

log = CrashingLog(activity_path(home), die_event, die_count)
loop = support.build_loop(home, replies, policy=policy,
                          approver=approver, log=log)
loop.run(loop.new_state())
raise SystemExit("driver finished without crashing - scenario broken")
'''


class CrashResumeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    # -- fixture -----------------------------------------------------------

    def _run_crash_driver(self, scenario: str) -> None:
        driver = self.tmp / f"crash_driver_{scenario}.py"
        driver.write_text(CRASH_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(driver), str(TESTS), str(self.home), scenario],
            cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(
            proc.returncode, 3,
            f"crash driver did not crash as scripted (rc={proc.returncode}); "
            f"stdout: {proc.stdout[-2000:]} stderr: {proc.stderr[-2000:]}")

    def _memory_lines(self) -> list[str]:
        return (self.home / "memory.md").read_text(encoding="utf-8").splitlines()

    def _log(self) -> ActivityLog:
        from fullstop.state import activity_path
        return ActivityLog(activity_path(self.home))

    def _notes_replies(self) -> list[str]:
        return [
            support.call_block("note", {"text": NOTE_ONE}),
            support.call_block("note", {"text": NOTE_TWO}),
            NOTES_REPLIES_MARKER,
        ]

    # -- scenario fidelity (must keep passing BEFORE and AFTER the fix) ----

    def test_crash_mid_run_leaves_running_checkpoint(self):
        """The fixture really is a crash-mid-run: checkpoint says "running"
        with the step half-done, while the log already recorded the executed
        call. This is the README's resume claim made concrete (memo defect 8).
        PASSES today - it verifies the scenario, not the fix."""
        self._run_crash_driver("notes")
        state = load_checkpoint(checkpoint_path(self.home))
        self.assertEqual(state.status, "running")
        self.assertEqual(state.steps_done, 1)
        # both notes executed before the crash
        self.assertEqual(len(self._memory_lines()), 2)
        entries = support.read_events(self.home)
        self.assertTrue(support.events_of(entries, "tool_call"))
        # ...but the durable checkpoint only knows about step 1
        self.assertLess(state.steps_done, 2)
        ok, first_bad = self._log().verify()
        self.assertEqual((ok, first_bad), (True, None))

    # -- the fix (all FAIL today) -------------------------------------------

    def test_resume_never_reexecutes_already_ran_calls(self):
        """Item 1 headline: no side effect happens twice. After resume,
        memory.md must still hold exactly the two lines written pre-crash —
        no matter which sub-mechanism (cursor replay or missing dedup guard)
        would have duplicated them."""
        self._run_crash_driver("notes")
        loaded = load_checkpoint(checkpoint_path(self.home))
        approver = ScriptedApprover([True])  # must never be asked
        provider = ScriptedModel(self._notes_replies(),
                                 start_cursor=loaded.script_cursor)
        loop = support.build_loop(self.home, self._notes_replies(),
                                  policy=Policy(), approver=approver,
                                  provider=provider)
        state = loop.run(loaded)
        self.assertEqual(state.status, "completed", state.failure)
        lines = self._memory_lines()
        self.assertEqual(
            len(lines), 2,
            f"side effects re-executed across resume: {lines!r}")
        self.assertEqual(sum("note one" in ln for ln in lines), 1)
        self.assertEqual(sum("note two" in ln for ln in lines), 1)
        ok, first_bad = self._log().verify()
        self.assertEqual((ok, first_bad), (True, None))

    def test_resume_never_reprompts_already_approved_request(self):
        """A call whose identical request was already approved pre-crash
        (approval_request + approval_response in the log) must not be
        re-prompted on replay: replay-skips are not new decisions."""
        self._run_crash_driver("notes")
        loaded = load_checkpoint(checkpoint_path(self.home))
        approver = ScriptedApprover([True])
        provider = ScriptedModel(self._notes_replies(),
                                 start_cursor=loaded.script_cursor)
        loop = support.build_loop(self.home, self._notes_replies(),
                                  policy=Policy(), approver=approver,
                                  provider=provider)
        loop.run(loaded)
        self.assertEqual(
            approver.prompts, [],
            f"resume re-prompted approval for already-approved calls: "
            f"{approver.prompts!r}")
        entries = support.read_events(self.home)
        resume_idx = max(i for i, e in enumerate(entries)
                         if e.get("event") == "resume")
        after = entries[resume_idx + 1:]
        self.assertEqual(
            support.events_of(after, "approval_request"), [],
            "approval_request events appended after resume")

    def test_replayed_call_gets_synthesized_observation(self):
        """A skipped ("may have executed") call must feed the model a
        synthesized observation saying so (memo wording), so the model can
        reason about the uncertain side effect instead of a fake result."""
        self._run_crash_driver("notes")
        loaded = load_checkpoint(checkpoint_path(self.home))
        provider = ScriptedModel(self._notes_replies(),
                                 start_cursor=loaded.script_cursor)
        loop = support.build_loop(self.home, self._notes_replies(),
                                  policy=Policy(), approver=ScriptedApprover([]),
                                  provider=provider)
        state = loop.run(loaded)
        self.assertEqual(state.status, "completed")
        synthesized = [m for m in state.messages
                       if isinstance(m, dict) and m.get("role") == "user"
                       and "may have executed" in str(m.get("content", ""))
                       and NOTE_TWO in str(m.get("content", ""))]
        self.assertTrue(
            synthesized,
            "no synthesized 'may have executed' observation for the replayed "
            f"call; final user messages: "
            f"{[str(m.get('content', ''))[:80] for m in state.messages[-4:]]}")


class PendingApprovalReplayTests(unittest.TestCase):
    """Item 1 + item 6 intersection: a call that was still PENDING approval
    when the crash hit (approval_request logged, no approval_response, no
    tool_result) did not execute. Resume must re-prompt it (persist requests,
    never verdicts) through the faithful full-payload display path."""

    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def _replies(self) -> list[str]:
        return [
            support.call_block("file_read", {"path": "data.txt"}),
            support.call_block("file_write",
                               {"path": "out.md", "content": LONG_WRITE}),
            "done",
        ]

    def test_pending_request_is_reprompted_with_full_payload(self):
        driver = self.tmp / "crash_driver_approval.py"
        driver.write_text(CRASH_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(driver), str(TESTS), str(self.home),
             "approval"],
            cwd=str(REPO), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 3, proc.stderr[-2000:])
        # crash landed before approval: no verdict, no execution
        entries = support.read_events(self.home)
        self.assertEqual(len(support.events_of(entries, "approval_request")), 1)
        self.assertEqual(support.events_of(entries, "approval_response"), [])
        self.assertFalse((self.home / "out.md").exists())

        from fullstop.state import activity_path
        loaded = load_checkpoint(checkpoint_path(self.home))
        approver = ScriptedApprover([True])
        provider = ScriptedModel(self._replies(),
                                 start_cursor=loaded.script_cursor)
        loop = support.build_loop(self.home, self._replies(), policy=Policy(),
                                  approver=approver, provider=provider)
        state = loop.run(loaded)
        self.assertEqual(state.status, "completed", state.failure)
        # the pending request WAS re-prompted (exactly once) ...
        self.assertEqual(len(approver.prompts), 1, approver.prompts)
        # ... through the faithful display: the FULL payload is visible
        self.assertIn(
            LONG_WRITE, approver.prompts[0],
            "re-prompt for a persisted pending request truncated the payload "
            "(must re-display faithfully, FIXLIST item 6)")
        self.assertIn("out.md", approver.prompts[0])
        self.assertEqual((self.home / "out.md").read_text(encoding="utf-8"),
                         LONG_WRITE)
        log = ActivityLog(activity_path(self.home))
        self.assertEqual(log.verify(), (True, None))


if __name__ == "__main__":
    unittest.main()
