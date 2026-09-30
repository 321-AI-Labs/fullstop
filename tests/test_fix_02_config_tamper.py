"""FIXLIST item 2 (must-fix): config-tamper persistence.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. Contract pinned
from the deliberation (turn-06-glm new finding 1 + PUSH, endorsed in the
final memo):

- load-time refusal: a manifest whose ``identity.home`` contains the config
  directory (the directory holding the manifest and/or policy file) places
  the gate's own constitution inside the agent's sandbox and must be
  rejected at load time ("home": "." is the canonical attack);
- a ``config_loaded`` event carrying sha256 of the effective manifest and
  policy bytes is appended at EVERY start and resume, so the chain proves
  which rules the agent was judged by;
- resume refuses on config-hash mismatch unless the operator passes an
  explicit override (pinned CLI flag: ``--allow-config-change`` on
  ``resume``);
- examples/docs stay free of in-sandbox homes (guard test).
"""

import hashlib
import json
import unittest
from pathlib import Path

import support
from fullstop.manifest import ManifestError, load_manifest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_configured_manifest(base: Path, name="agent",
                              max_steps=1) -> tuple[Path, Path, Path]:
    """Manifest + policy FILE + script, all in cfg/; home in cfg/ws (outside
    the config dir, as the fix demands of examples too). Returns
    (manifest_path, policy_path, home)."""
    cfg = base / name / "cfg"
    cfg.mkdir(parents=True)
    home = base / name / "ws"
    script = cfg / "script.json"
    replies = [
        support.call_block("note", {"text": "cfg run"}),
        support.call_block("note", {"text": "cfg run two"}),
        "done",
    ]
    if max_steps != 1:
        replies = [support.call_block("note", {"text": "cfg run"}), "done"]
    script.write_text(json.dumps(replies), encoding="utf-8")
    policy = cfg / "policy.json"
    policy.write_text(json.dumps(
        {"write_preapproved": ["notes/**", "memory.md"],
         "shell": {"allow": []}}), encoding="utf-8")
    manifest = cfg / "manifest.json"
    manifest.write_text(json.dumps({
        "identity": {"name": "cfg-test", "role": "tester",
                     "home": str(home)},
        "goal": "config goal",
        "policy": "policy.json",  # resolves relative to the manifest dir
        "provider": {"type": "scripted", "script_path": str(script)},
        "limits": {"max_steps": max_steps},
    }), encoding="utf-8")
    return manifest, policy, home


def run_cli(argv, approver=None):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from fullstop.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, approver=approver)
    return code, out.getvalue(), err.getvalue()


class ConfigTamperTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # -- load-time refusal (FAIL today) ------------------------------------

    def test_home_dot_inside_config_dir_rejected(self):
        """"home": "." puts the manifest (+policy) inside the sandbox: one
        approved file_write silently swaps the gate's rules."""
        cfg = self.tmp / "dot"
        cfg.mkdir()
        (cfg / "policy.json").write_text("{}", encoding="utf-8")
        (cfg / "script.json").write_text("[]", encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "n", "role": "r", "home": "."},
            "goal": "g",
            "policy": "policy.json",
            "provider": {"type": "scripted", "script_path": "script.json"},
            "limits": {"max_steps": 3},
        }), encoding="utf-8")
        with self.assertRaises(ManifestError) as ctx:
            load_manifest(manifest)
        self.assertIn("home", str(ctx.exception).lower())

    def test_home_parent_of_config_dir_rejected(self):
        cfg = self.tmp / "outer" / "cfg"
        cfg.mkdir(parents=True)
        (cfg / "policy.json").write_text("{}", encoding="utf-8")
        (cfg / "script.json").write_text("[]", encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "n", "role": "r", "home": ".."},
            "goal": "g",
            "policy": "policy.json",
            "provider": {"type": "scripted", "script_path": "script.json"},
            "limits": {"max_steps": 3},
        }), encoding="utf-8")
        with self.assertRaises(ManifestError):
            load_manifest(manifest)

    def test_policy_file_inside_home_rejected(self):
        """Even when home itself is fine, a policy file resolving inside the
        sandbox is a config-tamper vector (the policy IS the gate)."""
        cfg = self.tmp / "pol"
        cfg.mkdir()
        home = self.tmp / "pol" / "ws"
        home.mkdir()
        (home / "policy.json").write_text("{}", encoding="utf-8")
        (cfg / "script.json").write_text("[]", encoding="utf-8")
        manifest = cfg / "manifest.json"
        manifest.write_text(json.dumps({
            "identity": {"name": "n", "role": "r", "home": str(home)},
            "goal": "g",
            "policy": str(home / "policy.json"),
            "provider": {"type": "scripted", "script_path": "script.json"},
            "limits": {"max_steps": 3},
        }), encoding="utf-8")
        with self.assertRaises(ManifestError):
            load_manifest(manifest)

    def test_out_of_sandbox_home_still_loads(self):
        """GUARD (passes today, must keep passing): the normal layout —
        config in cfg/, home beside it — is not collateral damage."""
        manifest, _, _ = write_configured_manifest(self.tmp)
        loaded = load_manifest(manifest)
        self.assertTrue(loaded.identity.home.is_absolute())

    def test_shipped_examples_keep_config_out_of_sandbox(self):
        """GUARD for "keep examples/docs free of in-sandbox homes": every
        shipped example manifest must load under the in-sandbox-home rule."""
        examples = support.REPO_ROOT / "examples"
        shipped = sorted(examples.glob("*.json"))
        self.assertGreaterEqual(len(shipped), 2)
        for path in shipped:
            with self.subTest(example=path.name):
                load_manifest(path)  # must not raise

    # -- config_loaded anchoring (FAIL today) -------------------------------

    def test_config_loaded_event_with_hashes_on_run_and_resume(self):
        from fullstop.agent import ScriptedApprover
        manifest, policy, home = write_configured_manifest(self.tmp)
        code, _, err = run_cli(["run", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        code, _, err = run_cli(["resume", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        entries = support.read_events(home)
        loaded_events = support.events_of(entries, "config_loaded")
        self.assertGreaterEqual(
            len(loaded_events), 2,
            "config_loaded must be recorded at EVERY start and resume; got "
            f"{[e.get('event') for e in entries]}")
        for event in loaded_events:
            self.assertEqual(event.get("manifest_sha256"), _sha(manifest))
            self.assertEqual(event.get("policy_sha256"), _sha(policy))

    # -- refuse resume on mismatch (FAIL today) ------------------------------

    def test_resume_refuses_on_policy_change_without_override(self):
        from fullstop.agent import ScriptedApprover
        manifest, policy, home = write_configured_manifest(self.tmp)
        code, _, err = run_cli(["run", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)

        # The attack: one benign-looking edit broadens the gate's rules.
        policy.write_text(json.dumps(
            {"write_preapproved": ["notes/**", "memory.md", "**"],
             "shell": {"allow": []}}), encoding="utf-8")

        code, _, err = run_cli(["resume", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertNotEqual(code, 0,
                            "resume silently accepted a changed config")
        self.assertIn("config", (err + "").lower())

    def test_resume_on_config_change_with_explicit_override(self):
        from fullstop.agent import ScriptedApprover
        manifest, policy, home = write_configured_manifest(self.tmp)
        code, _, err = run_cli(["run", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        policy.write_text(json.dumps(
            {"write_preapproved": ["notes/**", "memory.md", "**"],
             "shell": {"allow": []}}), encoding="utf-8")
        # Pinned contract: the explicit operator override flag is
        # `--allow-config-change` on resume.
        code, out, err = run_cli(
            ["resume", "--manifest", str(manifest), "--allow-config-change"],
            approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
