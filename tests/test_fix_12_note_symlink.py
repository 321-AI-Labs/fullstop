"""FIXLIST item 12 (minor): NoteTool lacks FileWriteTool's
resolve-in-sandbox + resolved==literal protection.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1 wherever symlinks
can be created (on machines without the privilege these tests skip loudly
with a counted reason, matching the suite's existing symlink convention).
The defect (turn-04 critique 3, elevated in turn-03): NoteTool writes
memory.md by bare path concatenation; a symlink planted at memory.md turns
a PROMPTLESS pre-approved note write (both shipped policies pre-approve
memory.md) into a write to an attacker-chosen target outside the sandbox.

Frozen contract: note goes through the same chokepoint as file_write —
resolve in sandbox, require resolved == literal; a symlinked memory.md is
refused (fail closed), an ordinary memory.md append still works.
"""

import unittest
from pathlib import Path

import support
from fullstop.policy import Policy
from fullstop.types import ToolCall

NOTE_PREAPPROVED = Policy(write_preapproved=("memory.md",))


class NoteSymlinkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.h = support.gate_harness(self.tmp, policy=NOTE_PREAPPROVED)

    def test_note_refuses_symlinked_memory_target(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("original", encoding="utf-8")
        support.try_symlink(self, outside, self.h.home / "memory.md",
                            "fix12-note-symlink")
        result = self.h.execute(ToolCall("note", {"text": "pwned"}))
        self.assertFalse(
            result.ok,
            "a pre-approved note wrote THROUGH a symlinked memory.md")
        self.assertIn(result.error_code, ("sandbox_escape", "protected_target"),
                      result.error)
        self.assertEqual(outside.read_text(encoding="utf-8"), "original",
                         "the out-of-sandbox target was modified")

    def test_plain_note_append_still_works(self):
        """GUARD (passes today; must keep passing): the protection must not
        break the ordinary memory path."""
        result = self.h.execute(ToolCall("note", {"text": "hello"}))
        self.assertTrue(result.ok, result.error)
        text = (self.h.home / "memory.md").read_text(encoding="utf-8")
        self.assertIn("hello", text)


if __name__ == "__main__":
    unittest.main()
