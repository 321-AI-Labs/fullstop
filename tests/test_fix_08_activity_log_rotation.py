"""FIXLIST item 8 (must-fix): activity log append is O(n^2); no rotation.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 3): every append re-reads the WHOLE file (``_lines()``)
to validate the tail — an always-on agent writing ~6 events/step stalls
itself after weeks — and nothing ever rotates the log.

Frozen contract:

- steady-state appends never re-read the file: they chain from a cached
  last-line hash (probed deterministically by counting Path.read_text
  calls during appends — any full-file read shows up; the property under
  test is "no full-file re-read per append", the wall-clock behavior it
  stands for);
- size-based rotation (pinned constructor seam: ``max_segment_bytes``)
  moves the full head into timestamped segments in the same directory and
  starts a fresh live segment; the chain CONTINUES across segments (first
  entry of the new segment chains from the last hash of the previous one)
  and the global seq keeps counting;
- verify()/entries()/tail() walk ALL segments (the verifier walks segments;
  scripts/smoke.py chain verification is updated by the fix to match).
"""

import unittest
from pathlib import Path
from unittest import mock

import support
from fullstop.activity import ActivityLog


class ActivityRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "logs" / "activity.jsonl"

    def test_appends_do_not_reread_the_whole_file(self):
        log = ActivityLog(self.path)
        log.append("run_start", run_id="r")
        reads: list[str] = []
        real = Path.read_text

        def spy(self_path, *args, **kwargs):
            reads.append(str(self_path))
            return real(self_path, *args, **kwargs)

        with mock.patch("pathlib.Path.read_text", spy):
            for i in range(10):
                log.append("step", n=i, payload="x" * 64)
        self.assertEqual(
            reads, [],
            f"append re-read the full file {len(reads)} time(s) — O(n^2) "
            "append class; appends must chain from the cached last-line hash")

    def test_size_based_rotation_with_chain_continuing_across_segments(self):
        log = ActivityLog(self.path, max_segment_bytes=600)
        for i in range(30):
            log.append("step", n=i, payload=f"payload-{i:02d}-" + "y" * 40)
        segments = sorted(self.path.parent.glob("*.jsonl"))
        self.assertGreaterEqual(
            len(segments), 2,
            f"no rotation happened with max_segment_bytes=600; files: "
            f"{[s.name for s in segments]}")
        ok, first_bad = log.verify()
        self.assertEqual(
            (ok, first_bad), (True, None),
            "the verifier must walk ALL segments and accept the chain that "
            "continues across them")
        entries = list(log.entries())
        self.assertEqual(len(entries), 30)
        self.assertEqual([e["seq"] for e in entries], list(range(1, 31)),
                         "global seq must continue monotonically across "
                         "segments")

    def test_append_after_rotation_continues_the_chain(self):
        log = ActivityLog(self.path, max_segment_bytes=600)
        for i in range(30):
            log.append("step", n=i, payload=f"payload-{i:02d}-" + "y" * 40)
        log.append("run_end", status="completed")
        self.assertEqual(log.verify(), (True, None))
        self.assertEqual(len(list(log.entries())), 31)
        self.assertEqual(log.tail(3)[0]["seq"], 29)

    def test_guard_rotation_preserves_every_entry(self):
        """Nothing is DROPPED at rotation: the entry count equals the appends
        (covered by seq continuity above); this guard pins the total."""
        log = ActivityLog(self.path, max_segment_bytes=600)
        n = 0
        for i in range(40):
            log.append("tool_result", tool="note", ok=True, output="noted")
            n += 1
        self.assertEqual(len(list(log.entries())), n)


if __name__ == "__main__":
    unittest.main()
