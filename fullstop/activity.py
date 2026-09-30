"""Append-only, tamper-evident JSONL activity log.

Every line carries a chained hash: sha256(f"{prev}:{canonical_json(line_without_hash)}").
String values are scrubbed and truncated AT WRITE TIME, so credentials never
reach disk even in model replies.

v0.1.1 (FIXLIST item 8): appends are O(1) — they chain from the cached
last-line hash and never re-read the whole file (a bounded tail-window read
still validates the last line on every append, so an externally corrupted or
swapped tail is still refused). When the live segment grows past
``max_segment_bytes`` it rotates into a timestamped segment in the same
directory; the chain and the global ``seq`` CONTINUE across segments, and
``verify()``/``entries()``/``tail()`` walk all segments in order.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .redact import Redactor, truncate

_RESERVED = {"seq", "ts", "event", "prev", "hash"}
# How far back from EOF the bounded tail read may look for the last line.
_TAIL_WINDOW_BYTES = 1 << 20
_SEGMENT_RE = re.compile(r"^activity-\d{8}T\d{6,12}Z-(\d+)\.jsonl$")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class ActivityLogError(RuntimeError):
    pass


class ActivityLog:
    def __init__(self, path: Path, redactor: Redactor | None = None,
                 truncate_chars: int = 2000,
                 max_segment_bytes: int = 1_048_576) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._redactor = redactor
        self._truncate_chars = truncate_chars
        self._max_segment_bytes = max(1024, int(max_segment_bytes))
        # Chain head cache: {"hash": ..., "seq": ...} of the last appended
        # (or loaded) entry. O(1) appends chain from this, never from a
        # full-file re-read.
        self._tail: dict[str, Any] | None = None
        self._tail_loaded = False

    # -- scrubbing -----------------------------------------------------------

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            if self._redactor is not None:
                value = self._redactor.scrub(value)
            return truncate(value, self._truncate_chars)
        if isinstance(value, list):
            return [self._scrub(v) for v in value]
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                key = self._redactor.scrub(str(k)) if self._redactor else str(k)
                out[key] = self._scrub(v)
            return out
        return value

    # -- ordered segments ------------------------------------------------------

    def _segment_files(self) -> list[Path]:
        """All segment files in CHAIN order: rotated segments sorted by the
        seq stamped in their name, then the live file."""
        rotated: list[tuple[int, Path]] = []
        if self._path.parent.exists():
            for p in self._path.parent.glob("activity-*.jsonl"):
                m = _SEGMENT_RE.match(p.name)
                if m:
                    rotated.append((int(m.group(1)), p))
        rotated.sort(key=lambda sp: sp[0])
        return [p for _, p in rotated] + [self._path]

    def _read_last_entry(self) -> dict:
        """The last chain entry across segments, via a BOUNDED tail read.
        Raises ActivityLogError on a corrupt or unparseable tail; returns
        {} when nothing has been logged yet.

        v0.1.2 (FIXLIST2 item 3): a line larger than the tail window makes
        the window start MID-LINE, and the fragment it yields is not a
        valid entry — which used to wedge every future append. When the
        window read cannot produce a valid last line, the segment is
        re-read IN FULL (pathological case only; normal appends never hit
        it) so the real last line can be validated. A genuinely corrupt or
        tampered tail still raises — from the full read."""
        for path in reversed(self._segment_files()):
            if not path.exists():
                continue
            size = path.stat().st_size
            if size == 0:
                continue
            with path.open("rb") as fh:
                window = min(size, _TAIL_WINDOW_BYTES)
                fh.seek(-window, os.SEEK_END)
                data = fh.read()
            lines = [ln for ln in
                     data.decode("utf-8", errors="replace").split("\n")
                     if ln.strip()]
            try:
                obj = self._validate_last_line(lines)
            except ActivityLogError:
                if window >= size:
                    raise  # the window already saw the whole segment
                full_lines = [ln for ln in
                              path.read_text(encoding="utf-8",
                                             errors="replace").split("\n")
                              if ln.strip()]
                return self._validate_last_line(full_lines)
            if obj is not None:
                return obj
        return {}

    @staticmethod
    def _validate_last_line(lines: list[str]) -> dict | None:
        """Validate ``lines[-1]`` as the chain tail: None when the segment
        has no lines, the entry dict when valid; raises ActivityLogError on
        an unparseable, chain-field-missing, or hash-failing tail."""
        if not lines:
            return None
        line = lines[-1]
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ActivityLogError(
                f"activity log tail is not valid JSON: {e.msg}") from e
        if (not isinstance(obj, dict) or "seq" not in obj
                or "prev" not in obj or "hash" not in obj):
            raise ActivityLogError(
                "activity log tail is missing chain fields")
        expected = _sha256(
            f'{obj["prev"]}:{_canonical({k: v for k, v in obj.items() if k != "hash"})}')
        if expected != obj["hash"]:
            raise ActivityLogError(
                "activity log tail fails its chained hash (corrupt or "
                "tampered); refusing to extend")
        return obj

    def _load_tail(self) -> None:
        obj = self._read_last_entry()
        if obj:
            self._tail = {"hash": obj["hash"], "seq": obj["seq"]}
        else:
            self._tail = {"hash": "0", "seq": 0}
        self._tail_loaded = True

    # -- append --------------------------------------------------------------------

    def append(self, event: str, **fields: Any) -> str:
        clash = _RESERVED & set(fields)
        if clash:
            raise ValueError(f"reserved field names cannot be logged: {sorted(clash)}")
        if not self._tail_loaded:
            self._load_tail()
        else:
            # Cheap tamper tripwire: re-read ONLY the last line (bounded
            # window) and refuse to extend if it moved under us.
            current = self._read_last_entry()
            if (not current
                    or current.get("hash") != self._tail["hash"]
                    or current.get("seq") != self._tail["seq"]):
                raise ActivityLogError(
                    "activity log tail changed externally since the last "
                    "append (corrupt or tampered); refusing to extend")
        entry: dict[str, Any] = {
            "seq": self._tail["seq"] + 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        for key, value in fields.items():
            entry[key] = self._scrub(value)
        entry["prev"] = self._tail["hash"]
        entry["hash"] = _sha256(f'{entry["prev"]}:{_canonical(entry)}')
        line = _canonical(entry)
        # Size-based rotation: move the full live segment aside and start a
        # fresh one; the chain and seq continue across the boundary.
        if self._path.exists() and self._path.stat().st_size > 0:
            projected = self._path.stat().st_size + len(line) + 1
            if projected > self._max_segment_bytes:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "Z"
                rotated = self._path.with_name(
                    f"activity-{stamp}-{self._tail['seq']}.jsonl")
                os.replace(self._path, rotated)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._tail = {"hash": entry["hash"], "seq": entry["seq"]}
        return entry["hash"]

    # -- reading ----------------------------------------------------------------------

    def entries(self) -> Iterator[dict]:
        for path in self._segment_files():
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").split("\n"):
                if line.strip():
                    yield json.loads(line)

    def tail(self, n: int) -> list[dict]:
        return list(self.entries())[-n:]

    def verify(self) -> tuple[bool, int | None]:
        """Walk the whole chain across ALL segments; returns (ok, first_bad_seq
        or None)."""
        prev = "0"
        expected_seq = 1
        for path in self._segment_files():
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").split("\n"):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return False, expected_seq
                if not isinstance(obj, dict):
                    return False, expected_seq
                if obj.get("seq") != expected_seq or obj.get("prev") != prev:
                    return False, expected_seq
                body = {k: v for k, v in obj.items() if k != "hash"}
                if _sha256(f'{obj.get("prev")}:{_canonical(body)}') != obj.get("hash"):
                    return False, expected_seq
                prev = obj.get("hash")
                expected_seq += 1
        return True, None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
