"""The CLI seam: approver precedence, `run --ui` end-to-end over real
loopback HTTP (the approval answered from a real POST /api/approve), the
shipped acceptance demos (scripted-demo.json and writer-approval.json,
which exercises TWO prompts), and the `ui` subcommand.

`main(argv, approver)` stays the seam: tests that do not exercise the UI
approver inject ScriptedApprover and can never block on a tty.
"""

import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover
from fullstop.cli import main
from fullstop.ui_approver import rendezvous_dir_for

REPO = support.REPO_ROOT
EXAMPLES = REPO / "examples"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_json(port: int, method: str, path: str, body: dict | None = None,
              headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body) if body is not None else None
    hdrs = dict(headers or {})
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("X-Fullstop-UI", "1")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    return resp.status, json.loads(raw)


def wait_for_url(out: io.StringIO, timeout_s: float = 10.0) -> str:
    """Poll the captured stdout until the CLI prints its dashboard URL."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for line in out.getvalue().splitlines():
            if "http://127.0.0.1:" in line:
                return line.split("http://", 1)[1].split()[0].rstrip("/")
        time.sleep(0.05)
    raise AssertionError(f"no dashboard URL printed: {out.getvalue()!r}")


def run_cli_thread(argv, stdout: io.StringIO, approver=None):
    result: dict = {}

    def work():
        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(io.StringIO()):
            result["code"] = main(argv, approver)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.result = result  # type: ignore[attr-defined]
    return t


def wait_for_pending(port: int, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _, body = http_json(port, "GET", "/api/state")
        if body.get("pending"):
            return body["pending"][0]
        time.sleep(0.05)
    raise AssertionError("no pending approval card appeared")


def wait_join(t: threading.Thread, timeout_s: float = 30.0):
    t.join(timeout=timeout_s)
    assert not t.is_alive(), "CLI thread did not finish"


class SeamLawTests(unittest.TestCase):
    def test_injected_approver_wins(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=True)
        home = json.loads(manifest.read_text(encoding="utf-8")) \
            ["identity"]["home"]
        out = io.StringIO()
        approver = ScriptedApprover([True])
        port = free_port()
        t = run_cli_thread(["run", "--manifest", str(manifest), "--ui",
                            "--no-browser", "--port", str(port)], out,
                           approver=approver)
        url = wait_for_url(out)
        port = int(url.rsplit(":", 1)[1])
        wait_join(t)
        self.assertEqual(t.result["code"], 0)
        # the injected approver was consulted (the seam), NOT the UiApprover
        self.assertEqual(len(approver.prompts), 1)
        # no card was ever minted for this home
        rdir = rendezvous_dir_for(Path(home))
        self.assertEqual(list(rdir.glob("pending-*.json")), [])
        # the dashboard did serve while the run was live (proven by URL shape)
        self.assertIn("127.0.0.1", url)

    def test_default_approver_is_interactive_not_ui(self):
        """Without --ui the approver is InteractiveApprover: on a non-tty it
        fails unattended -> stopped_approval, and NO pending card is written
        (a UiApprover would have written one)."""
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=True)
        home = json.loads(manifest.read_text(encoding="utf-8")) \
            ["identity"]["home"]
        out, err = io.StringIO(), io.StringIO()
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("")  # force the non-tty path; never block
        try:
            with contextlib.redirect_stdout(out),                     contextlib.redirect_stderr(err):
                code = main(["run", "--manifest", str(manifest)])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["status"], "stopped_approval")
        rdir = rendezvous_dir_for(Path(home))
        self.assertEqual(list(rdir.glob("pending-*.json")), [])


class RunUiEndToEndTests(unittest.TestCase):
    def test_run_ui_answered_over_real_http(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=True)
        out = io.StringIO()
        port = free_port()
        t = run_cli_thread(["run", "--manifest", str(manifest), "--ui",
                            "--no-browser", "--port", str(port)], out)
        url = wait_for_url(out)
        port = int(url.rsplit(":", 1)[1])

        card = wait_for_pending(port)
        self.assertEqual(card["tool"], "file_write")
        self.assertIn("report.md", card["rendered"])

        status, body = http_json(port, "POST", "/api/approve",
                                 body={"id": card["id"],
                                       "decision": "approve"})
        self.assertEqual(status, 200)
        wait_join(t)
        self.assertEqual(t.result["code"], 0)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["status"], "completed")
        home = json.loads(manifest.read_text(encoding="utf-8")) \
            ["identity"]["home"]
        self.assertTrue((Path(home) / "report.md").exists())

    def test_run_ui_denied_over_real_http(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=True)
        out = io.StringIO()
        port = free_port()
        t = run_cli_thread(["run", "--manifest", str(manifest), "--ui",
                            "--no-browser", "--port", str(port)], out)
        url = wait_for_url(out)
        port = int(url.rsplit(":", 1)[1])
        card = wait_for_pending(port)
        status, _ = http_json(port, "POST", "/api/approve",
                              body={"id": card["id"], "decision": "deny"})
        self.assertEqual(status, 200)
        wait_join(t)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["status"], "completed")
        home = json.loads(manifest.read_text(encoding="utf-8")) \
            ["identity"]["home"]
        self.assertFalse((Path(home) / "report.md").exists())

    def test_browser_auto_open_called_with_ui(self):
        import fullstop.ui_server as ui_server
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=False)
        opened = []
        old_open = ui_server.webbrowser.open
        ui_server.webbrowser.open = lambda url: opened.append(url) or True
        self.addCleanup(setattr, ui_server.webbrowser, "open", old_open)
        out = io.StringIO()
        port = free_port()
        code = main(["run", "--manifest", str(manifest), "--ui",
                     "--port", str(port)], _stdout_for(out))
        self.assertEqual(code, 0)
        self.assertEqual(len(opened), 1)
        self.assertIn("127.0.0.1", opened[0])
        # --no-browser suppresses it
        opened.clear()
        out2 = io.StringIO()
        code = main(["run", "--manifest", str(manifest), "--ui",
                     "--no-browser", "--port", str(port)],
                    _stdout_for(out2))
        self.assertEqual(code, 0)
        self.assertEqual(opened, [])


def _stdout_for(out: io.StringIO):
    """A context manager that redirects stdout into ``out`` for the call."""
    return contextlib.redirect_stdout(out)


class ShippedDemoTests(unittest.TestCase):
    """DoD 1: both shipped acceptance demos work under run --ui. The
    workspaces are gitignored (examples/workspace-*/)."""

    def _run_demo(self, manifest_name: str, answers: list[bool]):
        manifest = EXAMPLES / manifest_name
        out = io.StringIO()
        port = free_port()
        t = run_cli_thread(["run", "--manifest", str(manifest), "--ui",
                            "--no-browser", "--port", str(port)], out)
        url = wait_for_url(out)
        port = int(url.rsplit(":", 1)[1])
        for answer in answers:
            card = wait_for_pending(port)
            status, _ = http_json(port, "POST", "/api/approve",
                                  body={"id": card["id"],
                                        "decision":
                                            "approve" if answer else "deny"})
            self.assertEqual(status, 200)
        wait_join(t)
        payload = json.loads(out.getvalue().strip().splitlines()[-1])
        return payload

    def test_scripted_demo_one_approval(self):
        payload = self._run_demo("scripted-demo.json", [True])
        self.assertEqual(payload["status"], "completed")

    def test_writer_approval_two_prompts(self):
        payload = self._run_demo("writer-approval.json", [True, False])
        self.assertEqual(payload["status"], "completed")

    @classmethod
    def tearDownClass(cls):
        # remove the demo workspaces the runs created (gitignored anyway)
        for d in EXAMPLES.glob("workspace-*"):
            shutil.rmtree(d, ignore_errors=True)


class UiSubcommandTests(unittest.TestCase):
    def test_ui_subprocess_serves_until_terminated(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        manifest = write_ui_manifest(Path(tmp.name), approval_prompt=False)
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "fullstop", "ui",
             "--manifest", str(manifest), "--no-browser",
             "--port", str(port)],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    status, body = http_json(port, "GET", "/api/state")
                    break
                except (OSError, ConnectionError):
                    time.sleep(0.1)
            else:
                self.fail("ui subcommand never served")
            self.assertEqual(status, 200)
            self.assertIn("snapshot", body)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()


def write_ui_manifest(base: Path, approval_prompt: bool) -> Path:
    """A scripted manifest whose second reply either asks for approval
    (report.md is NOT pre-approved) or not (notes only)."""
    mandir = base / "m"
    mandir.mkdir(parents=True, exist_ok=True)
    home = mandir / "home"
    (home / ".fullstop").mkdir(parents=True, exist_ok=True)
    if approval_prompt:
        replies = [
            support.call_block("file_write",
                               {"path": "report.md", "content": "the report"}),
            "done",
        ]
    else:
        replies = [
            support.call_block("note", {"text": "quick note"}),
            "done",
        ]
    script = mandir / "script.json"
    script.write_text(json.dumps(replies), encoding="utf-8")
    manifest = {
        "identity": {"name": "ui-e2e", "role": "tester", "home": str(home)},
        "goal": "exercise the ui",
        "policy": {"write_preapproved": ["notes/**", "memory.md"],
                   "shell": {"allow": []}, "web": {"allow_domains": []}},
        "provider": {"type": "scripted", "script_path": str(script)},
        "limits": {"max_steps": 8},
    }
    path = mandir / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
