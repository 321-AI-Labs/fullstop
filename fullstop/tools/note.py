"""NoteTool: append scrubbed, timestamped lines to types.NOTE_FILENAME.

The target file is FIXED — there is no filename parameter, so the gate's
policy matching can never drift from the tool's actual target. Classification
is CONSEQUENTIAL (gate table): the model cannot route around the write gate by
choosing note. Operators wanting frictionless memory put NOTE_FILENAME
("memory.md") in write_preapproved.
"""

from datetime import datetime, timezone
from pathlib import Path

from ..redact import Redactor
from ..sandbox import SandboxError, rel_posix, resolve_in_sandbox
from ..types import NOTE_FILENAME, ToolCall, ToolResult
from .base import Tool


class NoteTool(Tool):
    name = "note"

    def __init__(self, sandbox_root: Path, redactor: Redactor) -> None:
        self._root = Path(sandbox_root)
        self._redactor = redactor

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": (f"Append a memory line to {NOTE_FILENAME} at the "
                            "workspace root (gated like file_write)."),
            "args": {"text": "string (required)"},
        }

    def execute(self, call: ToolCall) -> ToolResult:
        text = call.args.get("text")
        if not isinstance(text, str) or not text:
            return ToolResult(ok=False, output="",
                              error="note requires a text string",
                              error_code="malformed_call")
        # v0.1.1 (FIXLIST item 12): the fixed target goes through the SAME
        # chokepoint as file_write — resolve in sandbox and require the
        # resolved target to equal the literal, so a symlink planted at
        # memory.md cannot redirect a (typically pre-approved, promptless)
        # note write outside the workspace.
        target = resolve_in_sandbox(self._root, NOTE_FILENAME)
        resolved_rel = rel_posix(self._root, target)
        if resolved_rel.casefold() != NOTE_FILENAME.casefold():
            raise SandboxError(
                f"note target resolves elsewhere in workspace: "
                f"{NOTE_FILENAME!r} -> {resolved_rel!r}")
        scrubbed = self._redactor.scrub(text)
        existing = ""
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        stamp = datetime.now(timezone.utc).isoformat()
        target.write_text(existing + f"- [{stamp}] {scrubbed}\n", encoding="utf-8")
        return ToolResult(ok=True, output="noted")
