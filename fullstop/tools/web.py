"""WebFetchTool: GET-only, timeout, size cap, content-type gate, injectable fetch.

v0.1.1 (FIXLIST item 5): on the REAL fetch path, every redirect hop is
re-validated against the COMPLETE domain allow/deny predicate BEFORE the
hop is requested — a custom redirect handler raises on any failing hop
(denylisted host, non-http(s) scheme, or — when an allowlist is configured —
any host not on it), so an approved URL cannot be walked into a host no one
approved. The original-URL denylist re-check is kept (hard red line, parity
with the gate). There is deliberately NO allowlist-membership veto at tool
level for the ORIGINAL url: an operator-approved fetch of a non-allowlisted
host executes via the injectable ``fetch`` seam.
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
    """Real network GET (no policy re-validation — use the tool's real path
    for that). Injected fakes replace this in tests and the smoke."""
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        content_type = resp.headers.get("Content-Type", "") or ""
        body = resp.read()
        code = resp.getcode() if hasattr(resp, "getcode") else 200
    return code, content_type, body


class _RedirectDenied(Exception):
    """Raised inside the redirect handler BEFORE a failing hop is requested."""


def _make_hop_validator(policy: WebPolicy) -> Callable[[str], None]:
    deny = {d.casefold() for d in policy.deny_domains}
    allow = {a.casefold() for a in policy.allow_domains}

    def validate(url: str) -> None:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (parts.scheme.lower() not in ("http", "https") or not host
                or host in deny
                or (allow and host not in allow)):
            raise _RedirectDenied(url)

    return validate


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-runs the full domain predicate on every Location hop."""

    def __init__(self, validator: Callable[[str], None]) -> None:
        super().__init__()
        self._validator = validator

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        self._validator(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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
            "description": ("HTTP GET a document (text-like bodies only, "
                            "size-capped, redirect hops re-validated)."),
            "args": {"url": "string (required), http(s) URL"},
        }

    def _real_fetch(self, url: str, timeout_s: float) -> tuple[int, str, bytes]:
        opener = urllib.request.build_opener(
            _PolicyRedirectHandler(_make_hop_validator(self._policy)))
        with opener.open(url, timeout=timeout_s) as resp:
            content_type = resp.headers.get("Content-Type", "") or ""
            body = resp.read()
            code = resp.getcode() if hasattr(resp, "getcode") else 200
        return code, content_type, body

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
            if self._fetch is default_fetch:
                _status, content_type, body = self._real_fetch(
                    url, self._policy.timeout_s)
            else:
                _status, content_type, body = self._fetch(
                    url, self._policy.timeout_s)
        except _RedirectDenied as e:
            denied = urlsplit(str(e)).hostname or str(e)
            return _err("domain_denied",
                        f"redirect hop denied by policy: {denied}")
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
