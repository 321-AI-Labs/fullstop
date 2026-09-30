"""FIXLIST item 6 (must-fix): approval prompts hide the payload.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 1, upgraded in turn-03 critique 1): approver prompts
truncate every argument to 40 chars, so the human approves what they cannot
see while the token cryptographically binds the FULL args.

Frozen contract:

- approver prompts (interactive AND scripted, i.e. every surface a human
  reads) show the COMPLETE request: generous per-arg and total render caps,
  clearly marked when truncation does happen;
- two payloads that share their first 40 characters must render
  distinguishably (the truncation-collision class);
- huge payloads are bounded and the elision is marked;
- approval_request events persist the full request args (guard);
- a persisted pending request is re-displayed faithfully on replay (that
  half lives in test_fix_01_crash_resume.py::PendingApprovalReplayTests —
  "persist requests, never verdicts; resume re-prompts", turn-04 law).
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import support
from fullstop.agent import InteractiveApprover, ScriptedApprover
from fullstop.policy import Policy
from fullstop.types import Action, GateDecision, ToolCall

LONG = "C" * 250
SHARED_40 = "S" * 40
TAIL_A = "TAIL-AAAA-visible-tail"
TAIL_B = "TAIL-BBBB-visible-tail"


class ApprovalDisplayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def _run(self, replies, approver, policy=None):
        loop = support.build_loop(self.home, replies,
                                  policy=policy or Policy(),
                                  approver=approver)
        state = loop.run(loop.new_state())
        return state

    def test_scripted_approver_prompt_shows_full_payload(self):
        approver = ScriptedApprover([True])
        state = self._run(
            [support.call_block("file_write",
                                {"path": "out.md", "content": LONG}),
             "done"],
            approver)
        self.assertEqual(state.status, "completed", state.failure)
        self.assertEqual(len(approver.prompts), 1)
        prompt = approver.prompts[0]
        self.assertIn("out.md", prompt)
        self.assertIn(
            LONG, prompt,
            f"approver prompt truncated the payload (40-char cap class); "
            f"prompt: {prompt!r}")

    def test_distinct_payloads_render_distinguishably(self):
        """The truncation-collision kill: two calls diverging only after
        char 40 must not render identically."""
        approver = ScriptedApprover([True, True])
        state = self._run(
            [support.call_block("file_write",
                                {"path": "a.md",
                                 "content": SHARED_40 + TAIL_A})
             + "\n"
             + support.call_block("file_write",
                                  {"path": "b.md",
                                   "content": SHARED_40 + TAIL_B}),
             "done"],
            approver)
        self.assertEqual(state.status, "completed", state.failure)
        self.assertEqual(len(approver.prompts), 2, approver.prompts)
        self.assertIn(TAIL_A, approver.prompts[0])
        self.assertIn(TAIL_B, approver.prompts[1])
        self.assertNotEqual(approver.prompts[0], approver.prompts[1])

    def test_huge_payload_is_capped_and_clearly_marked(self):
        # 250k chars: parseable by the protocol (block cap is 262,144) but
        # beyond any sane approval-render cap, so the elision marker must
        # appear. If the fixed cap is generous enough that 250k fits whole,
        # the cap is effectively uncapped for any legal single arg — not a
        # cap.
        huge = "D" * 250_000
        approver = ScriptedApprover([True])
        self._run(
            [support.call_block("file_write",
                                {"path": "big.md", "content": huge}),
             "done"],
            approver)
        self.assertTrue(approver.prompts, "the call never reached approval")
        prompt = approver.prompts[0]
        self.assertLess(
            len(prompt), len(huge),
            "the approver render must be bounded by a total cap")
        self.assertIn(
            "truncat", prompt.lower(),
            f"elision must be clearly marked in the prompt: {prompt!r}")

    def test_interactive_approver_prompt_shows_full_payload(self):
        """The REAL human surface (tty path): the printed question carries
        the complete payload. input() is faked; the stream reports a tty."""
        class _Tty(io.StringIO):
            def isatty(self):
                return True

        captured = []

        def fake_input(prompt=""):
            captured.append(prompt)
            return "n"

        approver = InteractiveApprover(stream=_Tty())
        call = ToolCall("file_write", {"path": "out.md", "content": LONG})
        with mock.patch("builtins.input", fake_input):
            decision = approver.approve(
                call, GateDecision(Action.APPROVAL_REQUIRED, "test"))
        self.assertFalse(decision)
        self.assertTrue(captured, "the approver never prompted")
        self.assertIn(
            LONG, captured[0],
            f"interactive prompt truncated the payload: {captured[0]!r}")
        self.assertIn("out.md", captured[0])

    def test_interactive_empty_answer_denies(self):
        """Mirror of the 'n' test above, for the quickstart's promise
        'answer `n` (or just Enter)': an EMPTY answer (just Enter) must
        DENY — the safe default, never an implicit approval."""
        class _Tty(io.StringIO):
            def isatty(self):
                return True

        captured = []

        def fake_input(prompt=""):
            captured.append(prompt)
            return ""

        approver = InteractiveApprover(stream=_Tty())
        call = ToolCall("file_write", {"path": "out.md", "content": LONG})
        with mock.patch("builtins.input", fake_input):
            decision = approver.approve(
                call, GateDecision(Action.APPROVAL_REQUIRED, "test"))
        self.assertFalse(decision)
        self.assertTrue(captured, "the approver never prompted")

    def test_interactive_nontty_still_shows_full_payload_before_denying(self):
        """The non-tty branch prints its prompt before auto-denying; what it
        prints must be the faithful render (what an operator reading the
        console log would have seen)."""
        class _NoTty(io.StringIO):
            def isatty(self):
                return False

        approver = InteractiveApprover(stream=_NoTty())
        call = ToolCall("file_write", {"path": "out.md", "content": LONG})
        printed = io.StringIO()
        with redirect_stdout(printed):
            decision = approver.approve(
                call, GateDecision(Action.APPROVAL_REQUIRED, "test"))
        self.assertFalse(decision)
        self.assertIn(LONG, printed.getvalue())

    def test_approval_request_event_persists_full_request(self):
        """GUARD (passes today; regression protection for 'persist
        requests'): the logged approval_request carries the complete args."""
        approver = ScriptedApprover([True])
        self._run(
            [support.call_block("file_write",
                                {"path": "out.md", "content": LONG}),
             "done"],
            approver)
        entries = support.read_events(self.home)
        requests = support.events_of(entries, "approval_request")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("args", {}).get("content"), LONG)


if __name__ == "__main__":
    unittest.main()
