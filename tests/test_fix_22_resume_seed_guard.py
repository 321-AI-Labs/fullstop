"""Final-review fix: resume of a pre-v0.1.2 system-only checkpoint seeds the goal.

DELIB-20260930-fullstop-final-release-review (converged): a checkpoint written
by v0.1.1 can hold a ``[system]``-only message list (a pre-fix step-0
artifact, or any hand-edited status forcing a retry). Fresh runs seed the
goal as a user turn since fix_20 — Z.ai-class endpoints reject system-only
lists with HTTP 400 code 1214 — but ``resume`` re-sent the old shape to the
provider unchanged. Frozen contract: on resume, when messages == [system]
exactly, append a user turn seeded from ``state.goal`` — the PERSISTED
checkpoint goal, never ``manifest.goal`` (``--allow-config-change`` can swap
the manifest under a resume) — and never a second time (idempotent by
construction: post-fix states always carry the user turn).
"""

import json
import unittest
from pathlib import Path

import support

MANIFEST_GOAL = "guard-check manifest goal qwfp 11"
PERSISTED_GOAL = "guard-check persisted goal zmvc 23"


def run_cli(argv):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from fullstop.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class ResumeSeedGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "ws"
        (self.home / ".fullstop").mkdir(parents=True)

    def _manifest(self) -> Path:
        cfg = self.base / "cfg"
        cfg.mkdir(parents=True)
        script = cfg / "script.json"
        script.write_text(json.dumps(["nothing more to do"]),
                          encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "s", "role": "t", "home": str(self.home)},
            "goal": MANIFEST_GOAL,
            "policy": {},
            "provider": {"type": "scripted", "script_path": str(script)},
            "limits": {"max_steps": 4},
        }), encoding="utf-8")
        return manifest

    def _write_checkpoint(self, manifest: Path, messages: list,
                          goal: str) -> None:
        """Hand-craft the checkpoint exactly as v0.1.1 would have left it."""
        state = {
            "run_id": "prefixfixstep0crash000000000001",
            "manifest_path": str(manifest),
            "goal": goal,
            "status": "running",
            "steps_done": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "script_cursor": 0,
            "messages": messages,
            "failure": None,
        }
        (self.home / ".fullstop" / "state.json").write_text(
            json.dumps(state, indent=2), encoding="utf-8")

    def _messages(self) -> list:
        state = json.loads(
            (self.home / ".fullstop" / "state.json").read_text(encoding="utf-8"))
        return state["messages"]

    def test_resume_of_system_only_checkpoint_seeds_goal(self):
        manifest = self._manifest()
        self._write_checkpoint(
            manifest,
            [{"role": "system", "content": "pre-fix system-only prompt"}],
            goal=PERSISTED_GOAL)
        code, _, _ = run_cli(["resume", "--manifest", str(manifest)])
        self.assertEqual(code, 0)
        messages = self._messages()
        self.assertEqual(messages[0]["role"], "system")
        # THE guard assertion: without it this index is the assistant turn
        # and the first provider request was a system-only list.
        self.assertEqual(messages[1]["role"], "user",
                         "resume must seed the goal as a user turn before "
                         "the first provider request")
        self.assertTrue(messages[1]["content"].startswith("Goal: "))
        self.assertIn(PERSISTED_GOAL, messages[1]["content"],
                      "seed comes from the persisted checkpoint goal")
        self.assertNotIn(MANIFEST_GOAL, messages[1]["content"],
                         "seed must NOT come from the (swappable) manifest")
        self.assertEqual(messages[2]["role"], "assistant",
                         "the model turn follows the seeded goal")

    def test_resume_never_double_seeds(self):
        manifest = self._manifest()
        seeded = "Goal: already seeded by new_state"
        self._write_checkpoint(
            manifest,
            [{"role": "system", "content": "post-fix system prompt"},
             {"role": "user", "content": seeded}],
            goal=PERSISTED_GOAL)
        code, _, _ = run_cli(["resume", "--manifest", str(manifest)])
        self.assertEqual(code, 0)
        messages = self._messages()
        goal_turns = [m for m in messages
                      if m.get("role") == "user"
                      and str(m.get("content", "")).startswith("Goal: ")]
        self.assertEqual(len(goal_turns), 1,
                         "an already-seeded resume must not gain a second "
                         "goal turn")
        self.assertEqual(goal_turns[0]["content"], seeded)


if __name__ == "__main__":
    unittest.main()
