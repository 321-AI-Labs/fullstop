"""File tools through resolve_in_sandbox — the filesystem chokepoint consumers.

NORMALIZATION (shared, normative — IDENTICAL to the gate's rel):

- ``literal_rel``  = args["path"].replace("\\\\", "/") with a leading "./"
  stripped (casefold only for MATCHING);
- ``resolved_rel`` = rel_posix(root, resolved_real_path) (casefold only for
  matching).
"""

from pathlib import Path

from ..policy import Policy
from ..redact import Redactor
from ..sandbox import SandboxError, rel_posix, resolve_in_sandbox
from ..types import ToolCall, ToolResult
from .base import Tool


def _normalize(user_path: str) -> str:
    rel = user_path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _malformed(message: str) -> ToolResult:
    return ToolResult(ok=False, output="", error=message, error_code="malformed_call")


class FileReadTool(Tool):
    name = "file_read"

    def __init__(self, sandbox_root: Path, redactor: Redactor,
                 policy: Policy) -> None:
        self._root = Path(sandbox_root)
        self._redactor = redactor
        self._policy = policy

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": "Read a UTF-8 text file from the workspace.",
            "args": {
                "path": "string (required), workspace-relative",
                "max_bytes": "int (optional, default 65536)",
            },
        }

    def execute(self, call: ToolCall) -> ToolResult:
        path = call.args.get("path")
        if not isinstance(path, str) or not path:
            return _malformed("file_read requires a non-empty path string")
        max_bytes = call.args.get("max_bytes", 65536)
        if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
                or max_bytes <= 0):
            return _malformed("max_bytes must be a positive integer")
        real = resolve_in_sandbox(self._root, path)
        literal_rel = _normalize(path)
        resolved_rel = rel_posix(self._root, real)
        # ALIAS-SCOPED protected check (normative): fire ONLY when the literal
        # rel was unprotected but resolves to a protected file (the symlink
        # ALIAS case). An operator-approved DIRECT protected read (literal
        # protected, gate returned ALLOW+token after approval) proceeds.
        if (self._policy.is_protected(resolved_rel)
                and not self._policy.is_protected(literal_rel)):
            return ToolResult(
                ok=False, output="",
                error=(f"protected target: {literal_rel} resolves to protected "
                       f"{resolved_rel}"),
                error_code="protected_target")
        # v0.1.1 (FIXLIST item 9): the READ is capped at the source — at most
        # max_bytes come off the disk; the file is never fully read first.
        with real.open("rb") as fh:
            data = fh.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        return ToolResult(ok=True, output=self._redactor.scrub(text))


class FileWriteTool(Tool):
    name = "file_write"

    def __init__(self, sandbox_root: Path, redactor: Redactor,
                 policy: Policy) -> None:
        self._root = Path(sandbox_root)
        self._redactor = redactor
        self._policy = policy

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": "Write a UTF-8 text file inside the workspace (overwrite).",
            "args": {
                "path": "string (required), workspace-relative",
                "content": "string (required)",
            },
        }

    def execute(self, call: ToolCall) -> ToolResult:
        path = call.args.get("path")
        if not isinstance(path, str) or not path:
            return _malformed("file_write requires a non-empty path string")
        content = call.args.get("content")
        if not isinstance(content, str):
            return _malformed("file_write requires a content string")
        # Scrub path AND content BEFORE any filesystem effect (DoD#4): a
        # secret must never land on disk, not even in a filename.
        scrubbed_path = self._redactor.scrub(path)
        scrubbed_content = self._redactor.scrub(content)
        real = resolve_in_sandbox(self._root, scrubbed_path)
        literal_rel = _normalize(scrubbed_path)
        resolved_rel = rel_posix(self._root, real)
        # Anti-aliasing for writes, outright: the resolved target must EQUAL
        # the literal spelling. NOTE (intentional, do not "repair"): this rule
        # FAILS CLOSED on legal-but-unusual Windows spellings whose realpath
        # differs from the literal (8.3 short names, trailing dots and
        # spaces) — blocking them is deliberate.
        if resolved_rel.casefold() != literal_rel.casefold():
            raise SandboxError(
                f"path resolves elsewhere in workspace: {literal_rel!r} -> "
                f"{resolved_rel!r}")
        # Belt (unreachable via the loop by construction — the gate never
        # ALLOWS a protected literal write, and resolved==literal here, so
        # this can only fire on direct misuse):
        if self._policy.is_protected(resolved_rel):
            return ToolResult(ok=False, output="",
                              error=f"protected path: {resolved_rel}",
                              error_code="protected_target")
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(scrubbed_content, encoding="utf-8")
        n = len(scrubbed_content.encode("utf-8"))
        return ToolResult(ok=True, output=f"wrote {n} bytes to {literal_rel}")


class FileListTool(Tool):
    name = "file_list"

    def __init__(self, sandbox_root: Path, redactor: Redactor,
                 policy: Policy | None = None) -> None:
        self._root = Path(sandbox_root)
        self._redactor = redactor
        self._policy = policy

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": ("List one level of a workspace directory "
                            "(protected entries are hidden unless the "
                            "listing itself was operator-approved)."),
            "args": {"path": "string (optional, default '.')"},
        }

    def execute(self, call: ToolCall) -> ToolResult:
        path = call.args.get("path", ".")
        if not isinstance(path, str):
            return _malformed("path must be a string")
        real = resolve_in_sandbox(self._root, path)
        rel = _normalize(path)
        # v0.1.1 (FIXLIST item 13): a listing of an UNPROTECTED directory
        # hides protected entries (names+sizes are still metadata leaks);
        # a protected directory can only be listed through the gate's
        # approval path, which reaches the registry as an operator-approved
        # call and then shows everything.
        listed_protected = (self._policy is not None
                            and self._policy.is_protected_listing(rel))
        lines = []
        for entry in sorted(real.iterdir(), key=lambda e: e.name.casefold()):
            if self._policy is not None and not listed_protected:
                entry_rel = (f"{rel}/{entry.name}" if rel not in (".", "")
                             else entry.name)
                if self._policy.is_protected_listing(entry_rel):
                    continue
            if entry.is_dir():
                lines.append(f"d\t-\t{entry.name}")
            else:
                size = entry.stat().st_size
                lines.append(f"f\t{size}\t{entry.name}")
        return ToolResult(ok=True, output=self._redactor.scrub("\n".join(lines)))
