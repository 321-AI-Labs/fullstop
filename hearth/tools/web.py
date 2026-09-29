"""WebFetchTool: GET-only, timeout, size cap, content-type gate, injectable fetch.

The denylist re-check is kept (hard red line, parity with the gate). There is
deliberately NO allowlist-membership veto at tool level: an operator-approved
fetch of a non-allowlisted host EXECUTES via the injectable ``fetch`` seam.
"""

import urllib.request
from typing import Callable

from ..policy import WebPolicy
from ..redact import Redactor, truncate
from ..types import ToolCall, ToolResult
from .base import Tool

FetchFn = Callable[[str, float], tuple[int, str, bytes]]

_OUTPUT_CAP = 65536
_ALLOWED_EXACT = {"application/json", "application/xml"}


def default_fetch(url: str, timeout_s: float) -> tuple[int, str, bytes]:
    """Real network GET. Injected fakes replace this in tests and the smoke."""
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        content_type = resp.headers.get("Content-Type", "") or ""
        body = resp.read()
        code = resp.getcode() if hasattr(resp, "getcode") else 200
    return code, content_type, body


def _err(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, output="", error=message, error_code=code)


class WebFetchTool(Tool):
    name = "web_fetch"

    def __init__(self, policy: WebPolicy, redactor: Redactor,
                 fetch: FetchFn = default_fetch) -> None:
        self._policy = policy
        self._redactor = redactor
        self._fetch = fetch

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": "HTTP GET a document (text-like bodies only, size-capped).",
            "args": {"url": "string (required), http(s) URL"},
        }

    def execute(self, call: ToolCall) -> ToolResult:
        from urllib.parse import urlsplit
        url = call.args.get("url")
        if not isinstance(url, str) or not url:
            return _err("malformed_url", "web_fetch requires a url string")
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() not in ("http", "https") or not host:
            return _err("malformed_url", f"malformed url: {url!r}")
        if host in {d.casefold() for d in self._policy.deny_domains}:
            return _err("domain_denied", f"domain denied: {host}")
        try:
            _status, content_type, body = self._fetch(url, self._policy.timeout_s)
        except Exception as e:
            return _err("io_error", f"{type(e).__name__}: {e}")
        ctype = (content_type or "").split(";")[0].strip().lower()
        if (not ctype or not ctype.startswith("text/")
                and ctype not in _ALLOWED_EXACT and not ctype.endswith("+xml")):
            return _err("unsupported_media_type",
                        f"unsupported media type: {ctype or '(none)'}")
        truncated_note = ""
        if len(body) > self._policy.max_bytes:
            body = body[:self._policy.max_bytes]
            truncated_note = "\n[truncated]"
        text = body.decode("utf-8", errors="replace")
        text = self._redactor.scrub(text)
        return ToolResult(ok=True, output=truncate(text, _OUTPUT_CAP) + truncated_note)
