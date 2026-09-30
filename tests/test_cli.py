"""In-process main(argv, approver) — no path can touch stdin — plus one real
`python -m fullstop status` subprocess proving the -m entry; exit codes and JSON
output; error JSON on stderr. Scenarios are pinned approval-free."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover
from fullstop.cli import main

REPO = support.REPO_ROOT


def write_manifest(base: Path, name="agent", max_steps=8) -> Path:
    """Approval-free scripted manifest: write_preapproved covers everything
    (incl. memory.md), shell allow [], web allow [], script has no shell/web."""
    mandir = base / name
    mandir.mkdir(parents=True, exist_ok=True)
    script = mandir / "script.json"
    replies = [
        support.call_block("note", {"text": "cli run"})
        + "\n" + support.call_block("file_write",
                                    {"path": "notes/a.md", "content": "A"}),
        "done",
    ]
    if max_steps == 1:
        replies = [
            support.call_block("note", {"text": "cli run"}),
            support.call_block("file_write",
                               {"path": "notes/b.md", "content": "B"}),
            "done",
        ]
    script.write_text(json.dumps(replies), encoding="utf-8")
    manifest = {
        "identity": {"name": "cli-test", "role": "tester",
                     "home": str(mandir / "home")},
        "goal": "cli goal",
        "policy": {
            "write_preapproved": ["notes/**", "memory.md"],
            "shell": {"allow": []},
            "web": {"allow_domains": []},
        },
        "provider": {"type": "scripted", "script_path": str(script)},
        "limits": {"max_steps": max_steps},
    }
    path = mandir / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def run_cli(argv, approver=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, approver=approver)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_run_completes_and_prints_json(self):
        manifest = write_manifest(self.base)
        approver = ScriptedApprover([])  # approval-free: must stay empty
        code, out, err = run_cli(["run", "--manifest", str(manifest),
                                  "--goal", "override goal"],
                                 approver=approver)
        self.assertEqual(code, 0, err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["steps_done"], 1)
        home = json.loads(manifest.read_text(encoding="utf-8"))["identity"]["home"]
        self.assertTrue((Path(home) / "memory.md").exists())
        self.assertTrue((Path(home) / "notes" / "a.md").exists())
        self.assertEqual(approver.prompts, [])  # nothing needed approval
        state = json.loads((Path(home) / ".fullstop" / "state.json").read_text(
            encoding="utf-8"))
        self.assertEqual(state["goal"], "override goal")
        self.assertEqual(state["manifest_path"], str(manifest.resolve()))

    def test_resume_after_guard_stop(self):
        manifest = write_manifest(self.base, "stopper", max_steps=1)
        code, out, _ = run_cli(["run", "--manifest", str(manifest)],
                               approver=ScriptedApprover([]))
        self.assertEqual(code, 0)  # guard-stop exits 0
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["status"],
                         "stopped_max_steps")
        # raise the limit and resume. v0.1.1 (FIXLIST item 2): editing the
        # manifest changes its hash, so the resume carries the explicit
        # operator override the config-tamper guard demands.
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["limits"]["max_steps"] = 8
        manifest.write_text(json.dumps(data), encoding="utf-8")
        code, out, err = run_cli(["resume", "--manifest", str(manifest),
                                  "--allow-config-change"],
                                 approver=ScriptedApprover([]))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["status"],
                         "completed")

    def test_log_tail_and_verify(self):
        manifest = write_manifest(self.base)
        run_cli(["run", "--manifest", str(manifest)],
                approver=ScriptedApprover([]))
        code, out, err = run_cli(["log", "--manifest", str(manifest),
                                  "--tail", "3"])
        self.assertEqual(code, 0, err)
        lines = [json.loads(line) for line in out.strip().splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[-1]["event"], "run_end")
        code, out, err = run_cli(["log", "--manifest", str(manifest),
                                  "--verify"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload, {"verified": True, "first_bad_seq": None})

    def test_status_prints_checkpoint(self):
        manifest = write_manifest(self.base)
        run_cli(["run", "--manifest", str(manifest)],
                approver=ScriptedApprover([]))
        code, out, err = run_cli(["status", "--manifest", str(manifest)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "completed")
        self.assertIn("steps_done", payload)

    def test_usage_errors_exit_2_with_json(self):
        for argv in (["bogus"], ["run"], []):
            code, out, err = run_cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("error", json.loads(err.strip().splitlines()[-1]))

    def test_runtime_error_exit_1_with_json(self):
        missing = self.base / "nope.json"
        code, out, err = run_cli(["run", "--manifest", str(missing)],
                                 approver=ScriptedApprover([]))
        self.assertEqual(code, 1)
        payload = json.loads(err.strip().splitlines()[-1])
        self.assertIn("error", payload)
        # status without a checkpoint
        manifest = write_manifest(self.base, "fresh")
        code, _, err = run_cli(["status", "--manifest", str(manifest)])
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(err.strip().splitlines()[-1]))

    def test_python_dash_m_entry_subprocess(self):
        manifest = write_manifest(self.base)
        run_cli(["run", "--manifest", str(manifest)],
                approver=ScriptedApprover([]))
        proc = subprocess.run(
            [sys.executable, "-m", "fullstop", "status",
             "--manifest", str(manifest)],
            cwd=str(REPO), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0,
                         f"stderr: {proc.stderr}")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "completed")


if __name__ == "__main__":
    unittest.main()
