"""Pure read-model over the artifacts the run loop already writes.

The UI never touches the loop, the gate, or the tools; it reads the
checkpoint JSON and the activity log (architecture law 1) and projects them
into dashboard-shaped data:

- ``load_snapshot`` — the checkpoint as a JSON-safe dict (or None);
- ``LogReader`` — a rotation-aware, offset-cached incremental reader: each
  poll reads only new bytes, survives segment rotation (the shrunk live file
  resets; the renamed segment is read once, then cached), and suppresses
  duplicates by the chain ``seq`` so no entry can be yielded twice;
- ``history`` — runs grouped by run_id from the log, each with the status
  badge the run ended with (``interrupted`` when a run never recorded
  run_end and is not the newest one — honest about crash-mid-run);
- ``verify`` — a thin wrapper over ActivityLog.verify reused as-is.

Nothing here writes, anywhere.
"""

import json
from pathlib import Path

from .activity import ActivityLog, _SEGMENT_RE
from .state import checkpoint_path

# Statuses that mean "the newest run is actively changing" for the live view.
_ACTIVE_STATUSES = ("running",)


def load_snapshot(home: Path) -> dict | None:
    """The checkpoint dict for ``home``, or None when no checkpoint exists.

    Raises json.JSONDecodeError / OSError upward: the dashboard shows them
    as an error state rather than guessing."""
    path = checkpoint_path(Path(home))
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class LogReader:
    """Incremental, rotation-aware activity-log reader (read-only)."""

    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._offsets: dict[str, int] = {}
        self._last_seq = 0

    def _segments(self) -> list[Path]:
        """Chain order: rotated segments by the seq stamped in their name,
        then the live file (mirrors ActivityLog._segment_files)."""
        rotated: list[tuple[int, Path]] = []
        log_dir = checkpoint_path(self._home).parent
        if log_dir.exists():
            for p in log_dir.glob("activity-*.jsonl"):
                m = _SEGMENT_RE.match(p.name)
                if m:
                    rotated.append((int(m.group(1)), p))
        rotated.sort(key=lambda sp: sp[0])
        return [p for _, p in rotated] + [log_dir / "activity.jsonl"]

    def _read_new_bytes(self, path: Path, offset: int) -> tuple[bytes, int]:
        """Bytes from ``offset`` to EOF, and the offset that consumes them.
        Only COMPLETE lines (terminated by \\n) are consumed; a torn tail
        line stays unread for the next poll."""
        try:
            size = path.stat().st_size
        except OSError:
            return b"", offset
        if size < offset:
            # The file shrank: it rotated (this name is a fresh file) — or
            # was replaced. Start over from the beginning; the seq guard
            # below makes re-reads harmless duplicates, never loss.
            offset = 0
        if size == offset:
            return b"", offset
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
        cut = data.rfind(b"\n")
        if cut < 0:
            return b"", offset  # torn line mid-append: wait for the newline
        return data[:cut + 1], offset + cut + 1

    def new_entries(self) -> list[dict]:
        """Entries appended since the previous call, chain order, no dupes.

        Malformed lines raise json.JSONDecodeError upward: the dashboard
        surfaces an error state instead of silently skipping (no silent
        truncation law applies to reading too)."""
        entries: list[dict] = []
        for path in self._segments():
            if not path.exists():
                continue
            offset = self._offsets.get(str(path), 0)
            data, new_offset = self._read_new_bytes(path, offset)
            self._offsets[str(path)] = new_offset
            for line in data.decode("utf-8", errors="replace").split("\n"):
                if not line.strip():
                    continue
                entry = json.loads(line)
                seq = entry.get("seq")
                if isinstance(seq, int) and seq > self._last_seq:
                    entries.append(entry)
                    self._last_seq = seq
        return entries

    @property
    def last_seq(self) -> int:
        return self._last_seq

    def reset(self) -> None:
        self._offsets.clear()
        self._last_seq = 0


def history(entries: list[dict]) -> list[dict]:
    """Group log entries into runs by run_id (run_start/resume boundaries).

    Each run: run_id, goal, started, ended, status badge, steps, token
    totals. A run whose run_end is missing shows ``interrupted`` unless it
    is the newest run (which may genuinely still be running)."""
    runs: list[dict] = []
    current: dict | None = None
    for entry in entries:
        event = entry.get("event")
        if event == "run_start":
            current = {
                "run_id": entry.get("run_id"),
                "goal": entry.get("goal", ""),
                "started": entry.get("ts"),
                "ended": None,
                "status": "running",
                "steps": 0,
                "tokens_in": 0,
                "tokens_out": 0,
            }
            runs.append(current)
        elif current is None:
            continue
        elif event == "resume":
            current["status"] = "running"
        elif event == "step":
            n = entry.get("n")
            if isinstance(n, int):
                current["steps"] = max(current["steps"], n)
        elif event == "model_reply":
            current["tokens_in"] += int(entry.get("input_tokens") or 0)
            current["tokens_out"] += int(entry.get("output_tokens") or 0)
        elif event == "run_end":
            current["status"] = entry.get("status", "unknown")
            current["ended"] = entry.get("ts")
    if runs:
        for run in runs[:-1]:
            if run["status"] == "running":
                run["status"] = "interrupted"
    return runs


def verify(home: Path) -> tuple[bool, int | None]:
    """Reuse the existing chain verifier, all segments, as-is.

    Guarded so the UI never CREATES the workspace log directory as a side
    effect of verifying: with nothing logged the chain is vacuously intact.
    (ActivityLog.__init__ would otherwise mkdir the directory.)"""
    log_dir = checkpoint_path(Path(home)).parent
    if not log_dir.exists():
        return True, None
    return ActivityLog(log_dir / "activity.jsonl").verify()


def snapshot_view(snapshot: dict | None) -> dict | None:
    """The header fields the run panel shows (numbers from real data only)."""
    if snapshot is None:
        return None
    return {
        "run_id": snapshot.get("run_id"),
        "goal": snapshot.get("goal"),
        "status": snapshot.get("status"),
        "steps_done": snapshot.get("steps_done", 0),
        "cost_usd": snapshot.get("cost_usd", 0.0),
        "tokens_in": snapshot.get("tokens_in", 0),
        "tokens_out": snapshot.get("tokens_out", 0),
        "failure": snapshot.get("failure"),
        "manifest_path": snapshot.get("manifest_path"),
        "live": snapshot.get("status") in _ACTIVE_STATUSES,
    }
