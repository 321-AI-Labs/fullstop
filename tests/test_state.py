"""Checkpoint round-trip, atomic replace, message scrubbing, dict fidelity."""

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (sys.path bootstrap for `import fullstop`)

from fullstop.redact import Redactor
from fullstop.state import (RunState, activity_path, checkpoint_path,
                          load_checkpoint, save_checkpoint)


def make_state() -> RunState:
    return RunState(
        run_id="abc123", manifest_path="m.json", goal="do the thing",
        status="running", steps_done=3, tokens_in=120, tokens_out=80,
        cost_usd=0.25, script_cursor=2,
        messages=[{"role": "system", "content": "sys"},
                  {"role": "assistant", "content": "hi"}],
        failure=None,
    )


class StateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.path = checkpoint_path(self.home)

    def test_canonical_paths(self):
        self.assertEqual(checkpoint_path(self.home),
                         self.home / ".fullstop" / "state.json")
        self.assertEqual(activity_path(self.home),
                         self.home / ".fullstop" / "activity.jsonl")

    def test_round_trip_fidelity(self):
        state = make_state()
        save_checkpoint(state, self.path)
        loaded = load_checkpoint(self.path)
        self.assertEqual(loaded.to_dict(), state.to_dict())
        self.assertEqual(loaded.run_id, "abc123")
        self.assertEqual(loaded.steps_done, 3)
        self.assertEqual(loaded.cost_usd, 0.25)
        self.assertEqual(loaded.script_cursor, 2)
        self.assertEqual(loaded.messages, state.messages)

    def test_from_dict_defaults(self):
        data = {"run_id": "x", "goal": "g", "status": "completed"}
        state = RunState.from_dict(data)
        self.assertEqual(state.steps_done, 0)
        self.assertIsNone(state.failure)
        self.assertEqual(state.messages, [])

    def test_atomic_replace_leaves_no_tmp(self):
        save_checkpoint(make_state(), self.path)
        save_checkpoint(make_state(), self.path)  # second save rewrites
        leftovers = [p for p in self.path.parent.iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        # and the file is valid JSON after the replace
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], "abc123")

    def test_messages_scrubbed_before_write(self):
        marker = "sk-FAKE-state"
        state = make_state()
        state.messages.append({"role": "assistant", "content": f"leak {marker}"})
        save_checkpoint(state, self.path, Redactor({"FULLSTOP_KEY": marker}))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(marker, text)
        self.assertIn("[REDACTED:FULLSTOP_KEY]", text)
        # the in-memory state is untouched
        self.assertIn(marker, state.messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
