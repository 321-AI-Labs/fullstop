"""Append-only, tamper-evident JSONL activity log.

Every line carries a chained hash: sha256(f"{prev}:{canonical_json(line_without_hash)}").
String values are scrubbed and truncated AT WRITE TIME, so credentials never
reach disk even in model replies. The first append onto an existing file
validates the tail and refuses to extend a corrupt or tampered last line.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .redact import Redactor, truncate

_RESERVED = {"seq", "ts", "event", "prev", "hash"}


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class ActivityLogError(RuntimeError):
    pass


class ActivityLog:
    def __init__(self, path: Path, redactor: Redactor | None = None,
                 truncate_chars: int = 2000) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._redactor = redactor
        self._truncate_chars = truncate_chars

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

    # -- reading the tail ------------------------------------------------------

    def _lines(self) -> list[str]:
        if not self._path.exists():
            return []
        return [ln for ln in
                self._path.read_text(encoding="utf-8").split("\n") if ln.strip()]

    def _validate_tail(self, line: str) -> dict:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ActivityLogError(
                f"activity log tail is not valid JSON: {e.msg}") from e
        if (not isinstance(obj, dict) or "seq" not in obj or "prev" not in obj
                or "hash" not in obj):
            raise ActivityLogError("activity log tail is missing chain fields")
        expected = _sha256(f'{obj["prev"]}:{_canonical({k: v for k, v in obj.items() if k != "hash"})}')
        if expected != obj["hash"]:
            raise ActivityLogError(
                "activity log tail fails its chained hash (corrupt or tampered); "
                "refusing to extend")
        return obj

    # -- append --------------------------------------------------------------------

    def append(self, event: str, **fields: Any) -> str:
        clash = _RESERVED & set(fields)
        if clash:
            raise ValueError(f"reserved field names cannot be logged: {sorted(clash)}")
        lines = self._lines()
        if lines:
            tail = self._validate_tail(lines[-1])
            prev = tail["hash"]
            seq = tail["seq"] + 1
        else:
            prev = "0"
            seq = 1
        entry: dict[str, Any] = {
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        for key, value in fields.items():
            entry[key] = self._scrub(value)
        entry["prev"] = prev
        entry["hash"] = _sha256(f'{prev}:{_canonical(entry)}')
        line = _canonical(entry)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return entry["hash"]

    # -- reading ----------------------------------------------------------------------

    def entries(self) -> Iterator[dict]:
        for line in self._lines():
            yield json.loads(line)

    def tail(self, n: int) -> list[dict]:
        return list(self.entries())[-n:]

    def verify(self) -> tuple[bool, int | None]:
        """Walk the whole chain; returns (ok, first_bad_seq or None)."""
        prev = "0"
        expected_seq = 1
        for line in self._lines():
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
