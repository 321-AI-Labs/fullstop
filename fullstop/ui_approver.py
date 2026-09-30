"""UiApprover: approvals through a decision-file handoff outside the sandbox.

The third Approver implementation (agent.Approver). When the gate demands a
human, the approver writes a pending card to a per-home RENDEZVOUS DIRECTORY
under the OS temp dir — deliberately OUTSIDE the workspace sandbox, so the
agent's own file_write tool can never reach (let alone forge) an approval —
and polls for a matching decision file written by the loopback dashboard
(ui_server). The card carries the COMPLETE protocol.render_request output
(never a truncated summary), scrubbed by the run's redactor first: secrets
never reach disk.

Fail-closed contract: on timeout, on an unreadable/writable-nowhere channel,
or on any OSError, the approver sets ``unattended = True`` and returns False,
which the loop's existing check (agent.py) turns into ``stopped_approval`` —
exactly the semantics of the non-tty console approver. A forged or
wrong-id decision file is ignored: the id is 128 bits of secrets randomness
minted per prompt, bound into both filenames, and the gate's hard denials
never reach an approver at all (gate law: approval can only upgrade
APPROVAL_REQUIRED, never DENY).
"""

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .protocol import render_request
from .redact import Redactor
from .types import GateDecision, ToolCall

DEFAULT_TIMEOUT_S = 300.0
POLL_INTERVAL_S = 0.2
ID_CHARS = 16
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# Pendings left behind by a hard-crashed run process are swept once they are
# definitively dead (a day past their write time).
_STALE_AFTER_S = 24 * 3600.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rendezvous_dir_for(home: Path) -> Path:
    """Per-home rendezvous directory under the per-user OS temp dir.

    Keyed by a hash of the resolved home so two workspaces never share cards;
    inside the temp dir, so resolve_in_sandbox can never reach it from the
    workspace (the sandbox rejects absolute paths, drive forms, ``..`` and
    any containment escape — a file_write aimed here dies as sandbox_escape).
    """
    key = hashlib.sha256(str(Path(home).resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / f"fullstop-ui-{key[:16]}"


def pending_path(directory: Path, approval_id: str) -> Path:
    return Path(directory) / f"pending-{approval_id}.json"


def decision_path(directory: Path, approval_id: str) -> Path:
    return Path(directory) / f"decision-{approval_id}.json"


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def write_json_atomic(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_decision(directory: Path, approval_id: str, approve: bool) -> None:
    """The ONLY write the dashboard performs (architecture law 4)."""
    write_json_atomic(
        decision_path(directory, approval_id),
        {"id": approval_id,
         "decision": "approve" if approve else "deny",
         "ts": _now_iso()})


def sweep_stale(directory: Path, now: float | None = None) -> int:
    """Delete pending/decision files older than _STALE_AFTER_S. Returns how
    many were removed. Never raises: cleanup is best-effort."""
    now = time.time() if now is None else now
    removed = 0
    directory = Path(directory)
    try:
        candidates = list(directory.glob("pending-*.json")) + \
            list(directory.glob("decision-*.json"))
    except OSError:
        return 0
    for path in candidates:
        try:
            if now - path.stat().st_mtime > _STALE_AFTER_S:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


class UiApprover:
    """Blocks on a decision file; fail-closed to ``stopped_approval``."""

    def __init__(self, home: Path, redactor: Redactor | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 poll_interval_s: float = POLL_INTERVAL_S,
                 rendezvous_dir: Path | None = None) -> None:
        self.home = Path(home)
        self._redactor = redactor
        self.timeout_s = float(timeout_s)
        self.poll_interval_s = max(0.02, float(poll_interval_s))
        self.directory = (Path(rendezvous_dir) if rendezvous_dir is not None
                          else rendezvous_dir_for(self.home))
        self.unattended = False
        self.last_card: dict | None = None
        sweep_stale(self.directory)

    # -- card construction ----------------------------------------------------

    def _card(self, call: ToolCall, decision: GateDecision) -> dict:
        def scrub(text: str) -> str:
            return self._redactor.scrub(text) if self._redactor else text

        return {
            "id": secrets.token_hex(ID_CHARS // 2),
            "home": str(self.home),
            "tool": scrub(call.name),
            "reason": scrub(decision.reason),
            "rendered": scrub(render_request(call)),
            "ts": _now_iso(),
            "timeout_s": self.timeout_s,
        }

    # -- the Approver seam --------------------------------------------------------

    def approve(self, call: ToolCall, decision: GateDecision) -> bool:
        self.unattended = False
        try:
            card = self._card(call, decision)
        except Exception:
            self._fail_closed()
            return False
        self.last_card = card
        p_path = pending_path(self.directory, card["id"])
        d_path = decision_path(self.directory, card["id"])
        try:
            write_json_atomic(p_path, card)
        except OSError:
            self._fail_closed()
            return False
        deadline = time.monotonic() + self.timeout_s
        try:
            while time.monotonic() < deadline:
                if not d_path.exists():
                    time.sleep(self.poll_interval_s)
                    continue
                try:
                    payload = json.loads(
                        d_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Unreadable or half-written: keep waiting; the deadline
                    # is the backstop. A well-formed file replaces it.
                    time.sleep(self.poll_interval_s)
                    continue
                if (isinstance(payload, dict)
                        and payload.get("id") == card["id"]
                        and payload.get("decision") in ("approve", "deny")):
                    approved = payload["decision"] == "approve"
                    self._cleanup(p_path, d_path)
                    return approved
                # Wrong id or malformed decision: ignore, keep waiting.
                time.sleep(self.poll_interval_s)
        except OSError:
            self._fail_closed()
            self._cleanup(p_path, d_path)
            return False
        # Timeout: nobody answered; reproduce unattended semantics and
        # remove the dead card so the dashboard stops offering it.
        self._fail_closed()
        self._cleanup(p_path, d_path)
        return False

    def _fail_closed(self) -> None:
        self.unattended = True

    def _cleanup(self, *paths: Path) -> None:
        for path in paths:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    # -- read side (shared with the dashboard) -------------------------------------

    def pending_cards(self, now: float | None = None) -> list[dict]:
        """Valid, un-expired, not-yet-decided cards in the rendezvous dir —
        what the dashboard shows. Malformed files are skipped, never shown."""
        now = time.time() if now is None else now
        cards: list[dict] = []
        try:
            paths = sorted(self.directory.glob("pending-*.json"))
        except OSError:
            return []
        for path in paths:
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(card, dict) or not valid_id(card.get("id")):
                continue
            if decision_path(self.directory, card["id"]).exists():
                continue  # already answered; the approver will consume it
            try:
                written = datetime.fromisoformat(str(card.get("ts"))) \
                    .timestamp()
                timeout_s = float(card.get("timeout_s", DEFAULT_TIMEOUT_S))
            except (ValueError, TypeError, OverflowError):
                continue
            if now - written > timeout_s:
                card = dict(card)
                card["expired"] = True
            cards.append(card)
        return cards
