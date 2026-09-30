"""FIXLIST2 item 3 (low): log-wedge on oversize lines.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1.1. The defect:
``_read_last_entry`` validates the last line through a bounded 1 MiB tail
window (fullstop/activity.py, _TAIL_WINDOW_BYTES). If any single log line is
larger than that window, the window starts MID-LINE: the fragment it reads
is not valid JSON (or lacks the chain fields), so every subsequent append
raises ActivityLogError -- the log is wedged forever, including from a
fresh process (the "app restarts and can never log again" case).

FIXLIST2 offers "and/or" defenses: bound ``log_truncate_chars`` at the
manifest level safely below the tail window (validated range), and/or make
the tail scan robust to a mid-line window start. This suite pins BOTH
halves, because each alone leaves a wedge path:

- the manifest bound alone cannot help: ``_scrub`` truncates each STRING
  value individually -- a single event whose args carry many sub-cap
  strings (e.g. a shell argv) composes a line far beyond the window while
  every value passes the cap. The composed-line test below reproduces
  exactly that under a perfectly ordinary truncate_chars=2000;
- the robust tail scan alone cannot help a misconfigured run: an operator
  setting a window-scale truncate_chars wedges the very first big event.

Frozen contract:

- manifest validation rejects ``log_truncate_chars`` at or above the 1 MiB
  tail window (any "safely below" bound satisfies this);
- ordinary values (the minimum 100, the default 2000, a generous 4096)
  stay accepted (guard, passes today);
- after a line larger than the tail window reaches the log -- a single
  oversize string, or a composed multi-value line under a normal cap --
  appends keep working: from the SAME instance and from a FRESH instance
  (restart), the chain still verifies, and entries() reads every event
  back intact.
"""

import json
import unittest
from pathlib import Path

import support
from fullstop.activity import ActivityLog, ActivityLogError
from fullstop.manifest import ManifestError, manifest_from_dict

# fullstop/activity.py _TAIL_WINDOW_BYTES = 1 << 20. Pinned here as a literal
# so a drift in the window silently changes what "oversize" means.
TAIL_WINDOW_BYTES = 1 << 20
# Large enough that no size-based rotation interferes with the scenario.
NO_ROTATION = 64_000_000


class ManifestTruncateBoundTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _data(self, log_truncate_chars: int) -> dict:
        home = self.tmp / "ws"
        script = self.tmp / "script.json"
        script.write_text('["done"]', encoding="utf-8")
        return {
            "identity": {"name": "t", "role": "t", "home": str(home)},
            "goal": "g",
            "policy": {},
            "provider": {"type": "scripted",
                         "script_path": str(script)},
            "limits": {"max_steps": 3},
            "log_truncate_chars": log_truncate_chars,
        }

    def test_manifest_rejects_window_scale_truncate_chars(self):
        """FAIL today: any int >= 100 is accepted. A value at or above the
        1 MiB tail window lets a single truncated-at-cap string produce a
        line the tail scan can never validate again."""
        for value in (TAIL_WINDOW_BYTES, 2_000_000):
            with self.subTest(log_truncate_chars=value):
                with self.assertRaises(ManifestError) as ctx:
                    manifest_from_dict(self._data(value), base_dir=self.tmp)
                self.assertIn(
                    "log_truncate_chars", str(ctx.exception),
                    "the rejection must name the offending field")

    def test_manifest_still_accepts_ordinary_truncate_chars(self):
        """GUARD (passes today; must keep passing): the validated range
        must not collide with sane operator values -- the minimum 100 (the
        floor FIXLIST item 16 tests against), the default 2000, a generous
        4096."""
        for value in (100, 2000, 4096):
            with self.subTest(log_truncate_chars=value):
                manifest = manifest_from_dict(self._data(value),
                                              base_dir=self.tmp)
                self.assertEqual(manifest.log_truncate_chars, value)


class OversizeLineWedgeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "activity.jsonl"

    def test_single_oversize_line_does_not_wedge_future_appends(self):
        """FAIL today: one line larger than the 1 MiB tail window wedges
        every later append (the window starts mid-line and the fragment
        fails validation), from the same instance AND from a fresh
        process."""
        log = ActivityLog(self.path, truncate_chars=4_000_000,
                          max_segment_bytes=NO_ROTATION)
        log.append("big", data="x" * 1_200_000)  # line > 1 MiB window
        log.append("after_same_instance", ok=True)
        fresh = ActivityLog(self.path, truncate_chars=2000,
                            max_segment_bytes=NO_ROTATION)
        fresh.append("after_restart", ok=True)
        ok, first_bad = fresh.verify()
        self.assertEqual((ok, first_bad), (True, None))
        events = [entry["event"] for entry in fresh.entries()]
        self.assertEqual(
            events, ["big", "after_same_instance", "after_restart"])
        big = list(fresh.entries())[0]
        self.assertEqual(big["data"], "x" * 1_200_000)

    def test_composed_oversize_line_under_normal_cap_does_not_wedge(self):
        """FAIL today -- and would STILL fail with only the manifest bound
        implemented, which is why the robust tail scan is pinned too:
        ``_scrub`` caps each STRING individually, so many sub-cap strings
        (a shell argv, a long listing) compose a line far beyond the tail
        window while every single value passes truncate_chars=2000."""
        log = ActivityLog(self.path, truncate_chars=2000,
                          max_segment_bytes=NO_ROTATION)
        log.append("tool_call", tool="shell",
                   args=["y" * 2000 for _ in range(700)])  # ~1.4 MB line
        fresh = ActivityLog(self.path, truncate_chars=2000,
                            max_segment_bytes=NO_ROTATION)
        fresh.append("after_restart", ok=True)  # wedges today
        ok, first_bad = fresh.verify()
        self.assertEqual((ok, first_bad), (True, None))

    def test_tampered_tail_still_refuses_after_the_fallback_fix(self):
        """(This was the flip-me wedge-existence proof; inverted in the same
        fix commit, as its docstring required.) The full-read fallback that
        unwedges oversize lines must not mask GENUINE tail corruption:
        appending onto a tampered last line still refuses loudly — the
        wedge fix loosened nothing about tamper detection."""
        log = ActivityLog(self.path, truncate_chars=2000,
                          max_segment_bytes=NO_ROTATION)
        log.append("one", ok=True)
        log.append("two", ok=True)
        # Tamper: rewrite the last line as valid canonical JSON with one
        # field flipped -- the chained hash no longer matches.
        lines = self.path.read_text(encoding="utf-8").splitlines()
        obj = json.loads(lines[-1])
        obj["ok"] = not obj.get("ok")
        lines[-1] = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(ActivityLogError):
            ActivityLog(self.path, truncate_chars=2000,
                        max_segment_bytes=NO_ROTATION).append(
                "after_tamper", ok=True)


if __name__ == "__main__":
    unittest.main()
