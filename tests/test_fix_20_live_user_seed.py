"""Live-check fix: fresh runs must seed the goal as an opening USER turn.

Found live against Z.ai (2026-09-30, glm-5.3-flash): the first provider
request carried a SYSTEM-ONLY message list, which Z.ai rejects with HTTP 400
code 1214 ("The messages parameter is illegal"). OpenAI tolerates a
system-only list; Z.ai does not, so the mock suite never saw it.

Frozen contract: a fresh run's first provider request is
[system, user(goal), ...] — the last message before the model turn is a
user turn carrying the EFFECTIVE goal (manifest goal, or the --goal
override applied before loop construction per FIXLIST item 4).
"""

import json
import unittest
from pathlib import Path

import support

MANIFEST_GOAL = "seed-check manifest goal plugh 7"
OVERRIDE_GOAL = "seed-check override goal xyzzy 42"


def run_cli(argv):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from fullstop.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class UserSeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _manifest(self) -> Path:
        cfg = self.base / "cfg"
        cfg.mkdir(parents=True)
        home = self.base / "ws"
        script = cfg / "script.json"
        script.write_text(json.dumps(["nothing more to do"]),
                          encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "s", "role": "t", "home": str(home)},
            "goal": MANIFEST_GOAL,
            "policy": {"write_preapproved": ["notes/**", "memory.md"]},
            "provider": {"type": "scripted", "script_path": str(script)},
            "limits": {"max_steps": 4},
        }), encoding="utf-8")
        return manifest

    def _checkpoint_messages(self, home: Path) -> list:
        state = json.loads(
            (home / ".fullstop" / "state.json").read_text(encoding="utf-8"))
        return state["messages"]

    def test_fresh_run_seeds_user_goal_turn(self):
        manifest = self._manifest()
        code, _, _ = run_cli(["run", "--manifest", str(manifest)])
        self.assertEqual(code, 0)
        messages = self._checkpoint_messages(self.base / "ws")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user",
                         "first request must end on a user turn, not system")
        self.assertIn(MANIFEST_GOAL, messages[1]["content"])

    def test_goal_override_reaches_seeded_user_turn(self):
        manifest = self._manifest()
        code, _, _ = run_cli(["run", "--manifest", str(manifest),
                              "--goal", OVERRIDE_GOAL])
        self.assertEqual(code, 0)
        messages = self._checkpoint_messages(self.base / "ws")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn(OVERRIDE_GOAL, messages[1]["content"])
        self.assertNotIn(MANIFEST_GOAL, messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
