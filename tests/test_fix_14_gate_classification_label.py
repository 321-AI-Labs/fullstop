"""FIXLIST item 14 (minor): gate decision log labels approval-gated
web_fetch as 'read-only'.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect (memo
defect 6): a web_fetch that pauses for approval (unlisted domain) is logged
with classification='read-only' because classify() only marks write-like
tools consequential — misleading in the audit table even though the
decision itself is correct.

Frozen contract: any call that can pause for human approval classifies as
'consequential' in the gate_decision log; an allowlisted web_fetch remains
read-only.
"""

import unittest
from pathlib import Path

import support
from fullstop.activity import ActivityLog
from fullstop.gate import Gate
from fullstop.policy import Policy, WebPolicy
from fullstop.state import activity_path


def _decisions(home: Path) -> list[dict]:
    return support.events_of(support.read_events(home), "gate_decision")


class GateClassificationLabelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def test_approval_gated_web_fetch_labeled_consequential(self):
        from fullstop.types import ToolCall
        log = ActivityLog(activity_path(self.home))
        gate = Gate(Policy(web=WebPolicy()), self.home, log)
        decision = gate.decide(ToolCall(
            "web_fetch", {"url": "https://example.com/page"}))
        self.assertEqual(decision.action.value, "approval_required")
        events = _decisions(self.home)
        self.assertEqual(events[-1]["tool"], "web_fetch")
        self.assertEqual(
            events[-1]["classification"], "consequential",
            "an approval-gated web_fetch is a consequential action waiting "
            "on a human; the audit label must say so")

    def test_allowlisted_web_fetch_stays_read_only(self):
        """GUARD: a domain-allowlisted fetch is genuinely read-only and its
        label must not change."""
        from fullstop.types import ToolCall
        log = ActivityLog(activity_path(self.home))
        gate = Gate(Policy(web=WebPolicy(allow_domains=("example.com",))),
                    self.home, log)
        decision = gate.decide(ToolCall(
            "web_fetch", {"url": "https://example.com/page"}))
        self.assertEqual(decision.action.value, "allow")
        self.assertEqual(_decisions(self.home)[-1]["classification"],
                         "read-only")


if __name__ == "__main__":
    unittest.main()
