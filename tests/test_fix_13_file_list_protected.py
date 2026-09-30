"""FIXLIST item 13 (minor): file_list must not enumerate protected
``.fullstop/`` metadata without approval.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect (memo
defect 7): file_list is unconditionally read-only, so the model can
enumerate the protected metadata directory (names + sizes) for free.

Frozen contract:

- a listing that would reveal protected entries (e.g. file_list "." in a
  workspace containing .fullstop/) omits them from the output;
- listing a protected directory itself (file_list ".fullstop") is
  consequential: APPROVAL_REQUIRED at the gate (classification
  'consequential'), not a free read-only ALLOW;
- with an operator approval, listing a protected directory executes
  (guard: the fix must not brick approved metadata inspection).
"""

import unittest
from pathlib import Path

import support
from fullstop.types import Action, ToolCall


class FileListProtectedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.h = support.gate_harness(self.tmp)  # default policy
        (self.h.home / "notes").mkdir()
        (self.h.home / "notes" / "a.md").write_text("A", encoding="utf-8")
        # emulate the metadata dir's real contents for the listing tests
        (self.h.home / ".fullstop" / "state.json").write_text("{}", encoding="utf-8")
        (self.h.home / ".fullstop" / "activity.jsonl").write_text("", encoding="utf-8")

    def test_directory_listing_hides_protected_entries(self):
        result = self.h.execute(ToolCall("file_list", {"path": "."}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("notes", result.output)
        self.assertNotIn(
            ".fullstop", result.output,
            "file_list enumerated protected .fullstop/ metadata without "
            "approval")

    def test_listing_protected_directory_requires_approval(self):
        call = ToolCall("file_list", {"path": ".fullstop"})
        self.assertEqual(
            self.h.gate.classify(call), "consequential",
            "file_list of a protected directory must classify as "
            "consequential")
        decision = self.h.gate.decide(call)
        self.assertEqual(
            decision.action, Action.APPROVAL_REQUIRED,
            f"listing .fullstop must pause for approval, got "
            f"{decision.action.value}: {decision.reason}")

    def test_listing_protected_directory_with_approval_executes(self):
        """GUARD: an operator-approved listing of the metadata dir still
        works (approval remains possible; no brick)."""
        result = self.h.execute(ToolCall("file_list", {"path": ".fullstop"}))
        self.assertTrue(result.ok, result.error)
        self.assertTrue(
            any(name in result.output for name in
                ("state.json", "activity.jsonl", "script.json")),
            f"approved listing did not show metadata: {result.output!r}")


if __name__ == "__main__":
    unittest.main()
