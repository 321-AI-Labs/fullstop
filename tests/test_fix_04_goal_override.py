"""FIXLIST item 4 (must-fix): --goal override never reaches the model.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(memo defect 1, confirmed at cli.py run branch): the CLI builds AgentLoop
(and with it the system prompt from manifest.goal) BEFORE setting
state.goal = args.goal, so the override changes only the checkpointed goal,
never what the model works toward.

The frozen contract: after `run --goal O`, the SYSTEM PROMPT (the first
message the provider receives, persisted in the checkpoint) must carry O and
must not carry the manifest's original goal. Asserting prompt CONTENT, not
just the checkpoint's goal field, kills the false-green class (turn-01 B).
"""

import json
import unittest
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover

MANIFEST_GOAL = "the manifest goal that must be overridden"
OVERRIDE_GOAL = "override goal xyzzy 42"


def run_cli(argv, approver=None):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from fullstop.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, approver=approver)
    return code, out.getvalue(), err.getvalue()


class GoalOverrideTests(unittest.TestCase):
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
            "identity": {"name": "g", "role": "t", "home": str(home)},
            "goal": MANIFEST_GOAL,
            "policy": {"write_preapproved": ["notes/**", "memory.md"]},
            "provider": {"type": "scripted", "script_path": str(script)},
            "limits": {"max_steps": 4},
        }), encoding="utf-8")
        return manifest

    def test_goal_override_reaches_system_prompt(self):
        manifest = self._manifest()
        code, out, err = run_cli(
            ["run", "--manifest", str(manifest), "--goal", OVERRIDE_GOAL],
            approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        home = json.loads(manifest.read_text(encoding="utf-8"))["identity"]["home"]
        state = json.loads(
            (Path(home) / ".fullstop" / "state.json").read_text(encoding="utf-8"))
        messages = state["messages"]
        self.assertTrue(messages)
        system = messages[0]
        self.assertEqual(system.get("role"), "system")
        prompt = system.get("content", "")
        self.assertIn(
            OVERRIDE_GOAL, prompt,
            "the --goal override must reach the system PROMPT the model "
            f"actually works toward; prompt head: {prompt[:200]!r}")
        self.assertNotIn(
            MANIFEST_GOAL, prompt,
            "the overridden manifest goal must not remain in the prompt")
        # the checkpointed goal field keeps working too (old assertion class)
        self.assertEqual(state["goal"], OVERRIDE_GOAL)

    def test_no_override_keeps_manifest_goal_in_prompt(self):
        """GUARD (passes today): without --goal the manifest goal is the
        prompt goal."""
        manifest = self._manifest()
        code, out, err = run_cli(["run", "--manifest", str(manifest)],
                                 approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        home = json.loads(manifest.read_text(encoding="utf-8"))["identity"]["home"]
        state = json.loads(
            (Path(home) / ".fullstop" / "state.json").read_text(encoding="utf-8"))
        self.assertIn(MANIFEST_GOAL, state["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
