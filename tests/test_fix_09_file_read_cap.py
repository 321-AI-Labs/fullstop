"""FIXLIST item 9 (minor): file_read full-reads before capping.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 4): ``real.read_bytes()[:max_bytes]`` pulls the WHOLE file
into memory before the cap — the same class as the web_fetch pre-read bug
(memo defect 4).

Frozen contract: the READ is capped at the source — at most ``max_bytes``
of the file are read (probed deterministically: Path.read_bytes must not be
used at all on the read path; a fixed implementation reads exactly the cap
through an open file handle). The visible behavior (correct capped output)
must not regress.
"""

import unittest
from pathlib import Path
from unittest import mock

import support
from fullstop.types import ToolCall


class FileReadCapTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.h = support.gate_harness(self.tmp)
        (self.h.home / "big.txt").write_bytes(b"x" * (2 * 1024 * 1024))

    def test_file_read_does_not_full_read_before_capping(self):
        def boom(self_path, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError(
                f"file_read pulled the whole file through read_bytes "
                f"({self_path}); the read itself must be capped")

        with mock.patch("fullstop.tools.file.Path.read_bytes", boom):
            result = self.h.execute(ToolCall(
                "file_read", {"path": "big.txt", "max_bytes": 1000}))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output, "x" * 1000,
                         "the capped read must return exactly the first "
                         "max_bytes worth of content")


if __name__ == "__main__":
    unittest.main()
