"""The loopback server: binding law, read routes, the decision-file write
(the ONLY write), request guards, wizard endpoints, tampered-chain verify."""

import contextlib
import http.client
import io
import json
import threading
import time
import unittest
from pathlib import Path

import support
from fullstop.activity import ActivityLog
from fullstop.policy import policy_from_dict
from fullstop.types import Action, GateDecision, ToolCall
from fullstop.ui_approver import (UiApprover, decision_path,
                                  rendezvous_dir_for)
from fullstop.ui_server import UiServer


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(port: int, method: str, path: str, body: dict | None = None,
            headers: dict | None = None) -> tuple[int, dict | str, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body) if body is not None else None
    hdrs = dict(headers or {})
    if payload is not None:
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    ctype = resp.getheader("Content-Type") or ""
    conn.close()
    if "json" in ctype:
        return resp.status, json.loads(raw), ctype
    return resp.status, raw, ctype


class ServerFixture:
    """A live UiServer over a temp home, plus helpers."""

    def __init__(self, testcase):
        self.tmp = support.temp_dir()
        testcase.addCleanup(self.tmp.cleanup)
        self.home = support.make_home(Path(self.tmp.name))
        self.port = free_port()
        self.server = UiServer(self.home, port=self.port)
        testcase.addCleanup(self.server.stop)
        self.url = self.server.start()

    def get(self, path, headers=None):
        return request(self.port, "GET", path, headers=headers)

    def post(self, path, body, headers=None):
        return request(self.port, "POST", path, body=body, headers=headers)


class BindingLawTests(unittest.TestCase):
    def test_binds_loopback_only(self):
        fix = ServerFixture(self)
        self.assertEqual(fix.server._httpd.server_address[0], "127.0.0.1")
        self.assertTrue(fix.url.startswith("http://127.0.0.1:"))

    def test_no_host_flag_exists_anywhere(self):
        from fullstop.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["ui", "--manifest", "whatever.json",
                         "--host", "0.0.0.0"])
        self.assertEqual(code, 2)
        self.assertIn("usage", err.getvalue())

    def test_foreign_host_header_refused(self):
        fix = ServerFixture(self)
        status, body, _ = fix.get("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_post_requires_local_ui_header(self):
        fix = ServerFixture(self)
        status, body, _ = fix.post("/api/approve",
                                   {"id": "a" * 16, "decision": "approve"})
        self.assertEqual(status, 403)


class ReadRouteTests(unittest.TestCase):
    def test_empty_state(self):
        fix = ServerFixture(self)
        status, body, _ = fix.get("/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(body["snapshot"])
        self.assertEqual(body["history"], [])
        self.assertEqual(body["pending"], [])

    def test_log_after_seq_and_rotation(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = ActivityLog(home / ".fullstop" / "activity.jsonl",
                              max_segment_bytes=1024)
            for i in range(50):
                log.append("step", n=i, pad="p" * 40)
            self.assertTrue(list((home / ".fullstop").glob("activity-*.jsonl")))
            port = free_port()
            server = UiServer(home, port=port)
            self.addCleanup(server.stop)
            server.start()
            status, body, _ = request(port, "GET", "/api/log?after_seq=0")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["items"]), 50)
            status2, body2, _ = request(
                port, "GET", f"/api/log?after_seq={body['last_seq']}")
            self.assertEqual(body2["items"], [])
            # incremental: only new entries after the next append
            log.append("run_end", status="completed")
            status3, body3, _ = request(
                port, "GET", f"/api/log?after_seq={body['last_seq']}")
            self.assertEqual([e["event"] for e in body3["items"]],
                             ["run_end"])

    def test_assets_served(self):
        fix = ServerFixture(self)
        status, html, ctype = fix.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("window.STRINGS", html)
        status, css, ctype = fix.get("/app.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)
        status, js, ctype = fix.get("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        status, body, _ = fix.get("/nope")
        self.assertEqual(status, 404)
        status, raw, _ = fix.get("/favicon.ico")
        self.assertEqual(status, 204)  # no console 404 noise

    def test_verify_reports_first_bad_seq(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = ActivityLog(home / ".fullstop" / "activity.jsonl")
            log.append("run_start", run_id="r1")
            log.append("step", n=1)
            log.append("step", n=2)
            # tamper line 2
            path = home / ".fullstop" / "activity.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            victim = json.loads(lines[1])
            victim["n"] = 999
            lines[1] = json.dumps(victim, sort_keys=True,
                                  separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            port = free_port()
            server = UiServer(home, port=port)
            self.addCleanup(server.stop)
            server.start()
            status, body, _ = request(port, "GET", "/api/verify")
            self.assertEqual(status, 200)
            self.assertFalse(body["ok"])
            self.assertEqual(body["first_bad_seq"], 2)


class ApproveRouteTests(unittest.TestCase):
    """POST /api/approve writes exactly one artifact: the decision file."""

    def setUp(self):
        self.tmp = support.temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.home = support.make_home(Path(self.tmp.name))
        self.rdir = Path(self.tmp.name) / "r"

    def _serve(self, home, rdir):
        server = UiServer(home, port=free_port())
        self.addCleanup(server.stop)
        server.start()
        # point the server's rendezvous at the test dir via its reader
        server._rendezvous = rdir
        server._pending_reader = UiApprover(home, rendezvous_dir=rdir)
        return server

    def test_approve_writes_only_the_decision_file(self):
        server = self._serve(self.home, self.rdir)
        approver = UiApprover(self.home, timeout_s=20, poll_interval_s=0.02,
                              rendezvous_dir=self.rdir)
        call = ToolCall("file_write", {"path": "report.md", "content": "x"})
        decision = GateDecision(Action.APPROVAL_REQUIRED,
                                "consequential write: report.md")
        result: dict = {}
        t = threading.Thread(
            target=lambda: result.setdefault(
                "v", approver.approve(call, decision)), daemon=True)
        t.start()
        # card visible through the server read side
        deadline = time.monotonic() + 5
        cards = []
        while time.monotonic() < deadline:
            _, body, _ = request(server.port, "GET", "/api/state")
            cards = body["pending"]
            if cards:
                break
            time.sleep(0.02)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["tool"], "file_write")
        self.assertIn("report.md", card["rendered"])

        before = support.snapshot_files(self.home)
        before_r = support.snapshot_files(self.rdir)
        status, body, _ = request(
            server.port, "POST", "/api/approve",
            body={"id": card["id"], "decision": "approve"},
            headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        t.join(timeout=5)
        self.assertTrue(result["v"])
        after = support.snapshot_files(self.home)
        self.assertEqual(after, before)  # workspace untouched by the answer
        new_r = support.snapshot_files(self.rdir) - before_r
        # the decision file appeared, and both files were consumed by the
        # approver right after
        self.assertEqual(new_r, set())

    def test_unknown_id_404_and_bad_body_400(self):
        server = self._serve(self.home, self.rdir)
        status, body, _ = request(
            server.port, "POST", "/api/approve",
            body={"id": "b" * 16, "decision": "approve"},
            headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 404)
        status, body, _ = request(
            server.port, "POST", "/api/approve",
            body={"id": "not-hex", "decision": "approve"},
            headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 400)


class WizardRouteTests(unittest.TestCase):
    def test_validate_and_save_through_http(self):
        fix = ServerFixture(self)
        bad = {"name": "", "role": "r", "home": str(fix.home),
               "goal": "g", "providerType": "scripted",
               "maxSteps": "abc"}
        status, body, _ = fix.post("/api/wizard/validate", {"form": bad},
                                   headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("maxSteps", body["field_errors"])
        self.assertIn("name", body["field_errors"])
        script = Path(fix.tmp.name) / "m" / "script.json"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('["done"]', encoding="utf-8")
        good = dict(bad, name="agent", maxSteps="4", scriptPath=str(script),
                    savePath=str(Path(fix.tmp.name) / "m" / "agent.json"))
        status, body, _ = fix.post("/api/wizard/validate", {"form": good},
                                   headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        status, body, _ = fix.post("/api/wizard/save", {"form": good},
                                   headers={"X-Fullstop-UI": "1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(Path(body["saved_path"]).exists())


if __name__ == "__main__":
    unittest.main()
