"""FIXLIST item 16 (minor): ``log_truncate_chars`` is validated in
manifest.py but never reaches ActivityLog.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-04 critique 4 / turn-06): the CLI constructs ActivityLog without the
manifest value, so activity logs always cap at the hardcoded 2000 — the
config promises what the log doesn't do. (The same item orders a sweep for
other validated-but-unwired manifest fields; that sweep is fix-side work —
this file freezes the one confirmed dead field's wiring.)
"""

import json
import unittest
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover


class LogTruncateWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_manifest_log_truncate_chars_reaches_activity_log(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from fullstop.cli import main
        cfg = self.base / "cfg"
        cfg.mkdir(parents=True)
        home = self.base / "ws"
        script = cfg / "script.json"
        script.write_text(json.dumps(["B" * 500]), encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "t", "role": "t", "home": str(home)},
            "goal": "g",
            "policy": {"write_preapproved": ["notes/**", "memory.md"]},
            "provider": {"type": "scripted", "script_path": str(script)},
            "limits": {"max_steps": 4},
            "log_truncate_chars": 100,  # minimum legal value
        }), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["run", "--manifest", str(manifest)],
                        approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err.getvalue())

        entries = support.read_events(home)
        replies = support.events_of(entries, "model_reply")
        self.assertTrue(replies, "no model_reply event was logged")
        stored = replies[0]["content"]
        self.assertNotEqual(
            stored, "B" * 500,
            "the activity log stored the full 500-char reply — the manifest's "
            "log_truncate_chars=100 never reached ActivityLog")
        self.assertLess(len(stored), 160,
                        f"stored content not capped near 100 chars: "
                        f"len={len(stored)}")
        self.assertIn("truncated", stored)


if __name__ == "__main__":
    unittest.main()
