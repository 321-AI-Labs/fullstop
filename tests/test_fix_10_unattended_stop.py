"""FIXLIST item 10 (minor): non-tty approval denial burns max_steps in a
spin.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 5): with no approver configured (or a non-tty interactive
approver auto-denying), the loop keeps spending steps re-asking for
approval until max_steps — an always-on agent left unattended burns its
whole budget asking a question nobody can hear.

Frozen contract (pinned status name: ``stopped_approval``):

- an approval demand that CANNOT reach a human (no approver configured, or
  a non-tty interactive approver) pauses the run cleanly: status
  ``stopped_approval`` (resumable), without spinning through the remaining
  steps;
- a denial by a PRESENT human (ScriptedApprover answering no) still just
  denies that one call — the run continues (guard, unchanged semantics);
- a stopped_approval run resumes and completes once an approver answers.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import support
from fullstop.agent import InteractiveApprover, ScriptedApprover
from fullstop.policy import Policy
from fullstop.types import RUN_STATUSES

UNPRE_APPROVED_BLOCK = support.call_block(
    "file_write", {"path": "unpre.md", "content": "x"})


def _spin_script() -> list[str]:
    return [UNPRE_APPROVED_BLOCK] * 3 + ["done"]


class UnattendedStopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def test_status_vocabulary_gains_stopped_approval(self):
        self.assertIn("stopped_approval", RUN_STATUSES)

    def test_no_approver_stops_cleanly_instead_of_spinning(self):
        loop = support.build_loop(self.home, _spin_script(), policy=Policy(),
                                  approver=None)
        state = loop.run(loop.new_state())
        self.assertEqual(
            state.status, "stopped_approval",
            f"unattended approval demand must pause cleanly, got "
            f"{state.status!r}")
        self.assertLessEqual(
            state.steps_done, 1,
            f"the loop spun through {state.steps_done} step(s) asking a "
            f"question no one could answer (max_steps was 10)")
        self.assertFalse((self.home / "unpre.md").exists())
        entries = support.read_events(self.home)
        self.assertEqual(entries[-1]["event"], "run_end")

    def test_nontty_interactive_approver_stops_cleanly(self):
        loop = support.build_loop(
            self.home, _spin_script(), policy=Policy(),
            approver=InteractiveApprover(stream=io.StringIO()))
        printed = io.StringIO()
        with redirect_stdout(printed):
            state = loop.run(loop.new_state())
        self.assertEqual(state.status, "stopped_approval",
                         f"got {state.status!r}")
        self.assertLessEqual(state.steps_done, 1)
        self.assertIn("no interactive terminal", printed.getvalue())

    def test_stopped_approval_run_is_resumable_with_an_approver(self):
        manifest = support.script_manifest(self.home, "unused", max_steps=10)
        loop1 = support.build_loop(self.home, _spin_script(), policy=Policy(),
                                  approver=None, manifest=manifest)
        state = loop1.run(loop1.new_state())
        self.assertEqual(state.status, "stopped_approval")
        from fullstop.state import checkpoint_path, load_checkpoint
        loaded = load_checkpoint(checkpoint_path(self.home))
        approver = ScriptedApprover([True] * 3)
        loop2 = support.build_loop(self.home, _spin_script(), policy=Policy(),
                                   approver=approver)
        state2 = loop2.run(loaded)
        self.assertEqual(state2.status, "completed", state2.failure)
        self.assertTrue((self.home / "unpre.md").exists())
        self.assertTrue(approver.prompts, "resume should ask the pending "
                                          "request once a human is present")

    def test_human_denial_still_lets_the_run_continue(self):
        """GUARD (passes today; must keep passing): a present human saying
        'n' denies that call only."""
        loop = support.build_loop(
            self.home,
            [UNPRE_APPROVED_BLOCK, "done"],
            policy=Policy(), approver=ScriptedApprover([False]))
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed", state.failure)
        # one acting step; the completing no-call turn does not count
        self.assertEqual(state.steps_done, 1)
        self.assertFalse((self.home / "unpre.md").exists())


if __name__ == "__main__":
    unittest.main()
