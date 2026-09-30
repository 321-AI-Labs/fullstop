"""The loopback activity-view server: reads what the loop writes; the ONE
thing it ever writes is the approval decision file.

Laws implemented here (UI-CHARTER-V02 architecture):

- **Loopback only.** The socket is bound to 127.0.0.1 and there is no host
  parameter anywhere in this module or the CLI (law 4). Requests whose Host
  header is not this loopback origin are refused (DNS-rebinding guard), and
  every POST must carry the ``X-Fullstop-UI`` header, which cross-site form
  posts cannot set (CSRF guard). Decisions are additionally bound to a
  128-bit per-prompt id the page can only learn by reading loopback JSON.
- **One write surface.** GET routes touch nothing; POST /api/approve writes
  ``decision-<id>.json`` through ui_approver.write_decision; the wizard
  route writes one NEW manifest file when asked explicitly (scope 4). The
  server cannot edit manifests, checkpoints, or the log — it holds no code
  path that opens any of them for writing.
- **The loop is untouched.** This module never imports the agent loop; it
  polls the checkpoint/log files the loop already wrote (law 1), and the
  run stays foreground with the dashboard on daemon threads.
"""

import json
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ui_assets, ui_model, ui_strings
from .ui_approver import (UiApprover, pending_path, valid_id,
                          write_decision, rendezvous_dir_for)
from .ui_wizard import save_manifest, validate_form

DEFAULT_PORT = 8624
_MAX_BODY_BYTES = 1 << 20
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class UiServerError(RuntimeError):
    pass


def _json_bytes(payload, status: int = 200) -> tuple[bytes, str, int]:
    body = json.dumps(payload).encode("utf-8")
    return body, "application/json; charset=utf-8", status


def _asset_bytes(text: str, content_type: str) -> tuple[bytes, str, int]:
    return text.encode("utf-8"), content_type, 200


class UiServer:
    """Owns the read model, the rendezvous reader, and the HTTP thread."""

    def __init__(self, home: Path, port: int = DEFAULT_PORT,
                 open_browser: bool = False) -> None:
        self.home = Path(home)
        self.port = int(port)
        self.open_browser = bool(open_browser)
        self._lock = threading.Lock()
        self._reader = ui_model.LogReader(self.home)
        self._entries: list[dict] = []
        self._rendezvous = rendezvous_dir_for(self.home)
        self._pending_reader = UiApprover(self.home,
                                          rendezvous_dir=self._rendezvous)
        self._httpd: _Httpd | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    # -- read side ------------------------------------------------------------

    def _pump(self) -> None:
        try:
            self._entries.extend(self._reader.new_entries())
        except (json.JSONDecodeError, OSError):
            # Corrupt or mid-append tail: keep what parsed; the verifier
            # button is the honest oracle for a damaged chain.
            self._reader.reset()

    def state_snapshot(self) -> dict:
        with self._lock:
            self._pump()
            try:
                snapshot = ui_model.snapshot_view(ui_model.load_snapshot(self.home))
            except (json.JSONDecodeError, OSError) as e:
                snapshot = {"error": f"{type(e).__name__}: {e}"}
            return {
                "snapshot": snapshot,
                "history": ui_model.history(self._entries),
                "pending": self._pending_reader.pending_cards(),
            }

    def log_after(self, after_seq: int) -> dict:
        with self._lock:
            self._pump()
            items = [e for e in self._entries
                     if isinstance(e.get("seq"), int) and e["seq"] > after_seq]
            return {"items": items,
                    "last_seq": self._reader.last_seq}

    def verify_chain(self) -> dict:
        ok, first_bad = ui_model.verify(self.home)
        return {"ok": ok, "first_bad_seq": first_bad,
                "entries": len(self._entries)}

    # -- write side (decision file only) ---------------------------------------

    def record_decision(self, approval_id: str, approve: bool) -> None:
        write_decision(self._rendezvous, approval_id, approve)

    def pending_exists(self, approval_id: str) -> bool:
        return pending_path(self._rendezvous, approval_id).exists()

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> str:
        """Bind loopback (trying successive ports if busy) and start the
        serving thread. Returns the URL."""
        last_error: OSError | None = None
        for port in range(self.port, self.port + 24):
            try:
                httpd = _Httpd(("127.0.0.1", port), _Handler)
                break
            except OSError as e:  # port busy -> next
                last_error = e
        else:
            raise UiServerError(f"no free loopback port near {self.port}: "
                                f"{last_error}")
        httpd.ui = self
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(target=httpd.serve_forever,
                                        kwargs={"poll_interval": 0.1},
                                        name="fullstop-ui", daemon=True)
        self._thread.start()
        if self.open_browser:
            try:
                webbrowser.open(self.url)
            except Exception as e:  # noqa: BLE001 - note only, never fatal
                print(ui_strings.BROWSER_OPEN_FAILED.format(error=e))
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def serve_forever(self) -> None:
        """Foreground serve (the `ui` subcommand); stops on KeyboardInterrupt."""
        if self._httpd is None:
            self.start()
        assert self._httpd is not None
        try:
            self._httpd.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    ui: UiServer


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fullstop-ui"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet: the dashboard is not a logger
        pass

    # -- helpers -------------------------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload, status: int = 200) -> None:
        body, ctype, code = _json_bytes(payload, status)
        self._send(body, ctype, code)

    def _loopback_host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        return host in {f"{h}:{self.server.ui.port}" for h in _LOCAL_HOSTS}

    def _guard(self) -> bool:
        """Refuse anything that is not plainly this loopback origin."""
        if not self._loopback_host_ok():
            self._send_json({"error": ui_strings.ERROR_FORBIDDEN_HOST}, 403)
            return False
        return True

    def _read_post_json(self) -> dict | None:
        if not self._guard():
            return None
        if self.headers.get("X-Fullstop-UI") != "1":
            self._send_json({"error": ui_strings.ERROR_FORBIDDEN_HEADER}, 403)
            return None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY_BYTES:
            self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                reason="bad Content-Length")}, 400)
            return None
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                reason=str(e))}, 400)
            return None
        if not isinstance(payload, dict):
            self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                reason="body must be a JSON object")}, 400)
            return None
        return payload

    # -- routes ----------------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        if not self._guard():
            return
        ui = self.server.ui
        path = self.path.split("?", 1)[0]
        if path == "/":
            strings_json = json.dumps(ui_strings.STRINGS)
            body, ctype, status = _asset_bytes(
                ui_assets.render_html(strings_json), "text/html; charset=utf-8")
            self._send(body, ctype, status)
        elif path == "/app.css":
            self._send(*_asset_bytes(ui_assets.APP_CSS, "text/css; charset=utf-8"))
        elif path == "/app.js":
            self._send(*_asset_bytes(ui_assets.APP_JS,
                                     "text/javascript; charset=utf-8"))
        elif path == "/api/state":
            self._send_json(ui.state_snapshot())
        elif path == "/api/log":
            try:
                after = int(self.path.split("after_seq=", 1)[1].split("&", 1)[0])
            except (IndexError, ValueError):
                after = 0
            self._send_json(ui.log_after(after))
        elif path == "/api/verify":
            self._send_json(ui.verify_chain())
        elif path == "/favicon.ico":
            self._send(b"", "image/x-icon", 204)  # no icon; no console 404
        else:
            self._send_json({"error": ui_strings.ERROR_NOT_FOUND.format(
                path=path)}, 404)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_POST(self):  # noqa: N802
        ui = self.server.ui
        payload = self._read_post_json()
        if payload is None:
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/approve":
            approval_id = payload.get("id")
            decision = payload.get("decision")
            if not valid_id(approval_id) or decision not in ("approve", "deny"):
                self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                    reason="id/decision")}, 400)
                return
            if not ui.pending_exists(approval_id):
                self._send_json({"error": ui_strings.ERROR_NO_PENDING}, 404)
                return
            ui.record_decision(approval_id, decision == "approve")
            self._send_json({"ok": True})
        elif path == "/api/wizard/validate":
            form = payload.get("form")
            if not isinstance(form, dict):
                self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                    reason="form")}, 400)
                return
            self._send_json(validate_form(form))
        elif path == "/api/wizard/save":
            form = payload.get("form")
            if not isinstance(form, dict):
                self._send_json({"error": ui_strings.ERROR_BAD_POST.format(
                    reason="form")}, 400)
                return
            self._send_json(save_manifest(form))
        else:
            self._send_json({"error": ui_strings.ERROR_NOT_FOUND.format(
                path=path)}, 404)
