"""FIXLIST2 item 2 (medium): stopped_context unreachable through the shipped
provider adapter.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1.1. The defect:
OpenAICompatProvider converts an HTTPError into ProviderError(f"http
{code}") and discards the response body (fullstop/provider.py, both the
HTTPError except-arm and the non-200 getcode() branch), so
agent._is_context_error -- which matches on body phrases like "context
length" / "context_length_exceeded" -- never fires on a real endpoint.
Every real OpenAI-compatible server answers a context-exhaustion request
with a 400 whose BODY carries the diagnosis; the shipped adapter therefore
always lands the run in generic ``failed`` instead of ``stopped_context``
(FIXLIST item 3's whole contract, unreachable in production).

Frozen contract (FIXLIST2 item 2):

- the ProviderError message for a non-200 response carries the HTTP status
  code AND the (decoded) error response body, on BOTH adapter paths:
  the HTTPError exception path and the non-200-without-exception path;
- the carried body is size-capped (a multi-megabyte error page must not
  become a multi-megabyte exception string -- it flows into state.failure
  and the checkpoint, neither of which truncates);
- the API key never appears in any ProviderError message;
- end to end: a REAL loopback HTTP endpoint (stdlib http.server on
  127.0.0.1, ephemeral port -- zero external network, same pattern as
  tests/test_fix_05_redirect_chain.py) serving a realistic
  context-exhaustion 400 body drives the unmodified adapter (no fake
  opener -- the real urllib path) through an AgentLoop, and the run must
  land in ``stopped_context`` with the context diagnosis visible in
  state.failure.

Loopback-only; if the socket cannot be bound the tests skip loudly.
"""

import http.server
import io
import json
import os
import threading
import unittest
import urllib.error
from pathlib import Path

import support
from fullstop.manifest import Identity, Limits, Manifest, ProviderConfig
from fullstop.policy import Policy
from fullstop.provider import OpenAICompatProvider, ProviderError

ENV = "FULLSTOP_R2_PROVIDER_KEY"
KEY = "sk-FAKE-r2-adapter-key"

# Realistic OpenAI-compatible context-exhaustion error body (400).
CONTEXT_ERROR_BODY = json.dumps({
    "error": {
        "message": ("This model's maximum context length is 16385 tokens. "
                    "However, you requested 20000 tokens. Please reduce "
                    "the length of the messages."),
        "type": "invalid_request_error",
        "param": None,
        "code": "context_length_exceeded",
    }
}).encode("utf-8")

# Any cap at or under this keeps ProviderError text at log scale (the
# message flows into state.failure, run_end logging and the checkpoint;
# none of those truncate it).
MAX_REASONABLE_ERROR_TEXT_CHARS = 100_000


class _Context400Handler(http.server.BaseHTTPRequestHandler):
    """Every POST gets the realistic context-exhaustion 400."""

    server_version = "fullstop-r2fix"

    def do_POST(self):  # noqa: N802 (http.server API)
        server = self.server
        server.requests.append({
            "path": self.path,
            "auth": self.headers.get("Authorization"),
        })
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(CONTEXT_ERROR_BODY)))
        self.end_headers()
        self.wfile.write(CONTEXT_ERROR_BODY)

    def log_message(self, *args):  # keep test output clean
        pass


class _FakeResponse:
    def __init__(self, code, body: bytes):
        self._code = code
        self._body = body

    def getcode(self):
        return self._code

    def read(self, n=-1):  # real response objects accept a size limit
        return self._body


class AdapterErrorBodyTests(unittest.TestCase):
    """Fake-opener unit pins for both adapter branches (FAIL today)."""

    def setUp(self):
        self.cfg = ProviderConfig(type="openai_compat",
                                  base_url="https://api.example/v1/",
                                  api_key_env=ENV, model="test-model",
                                  timeout_s=5.0)
        os.environ[ENV] = KEY
        self.addCleanup(os.environ.pop, ENV, None)

    def test_http_error_body_reaches_provider_error_message(self):
        """The path real 400s take: urllib raises HTTPError carrying the
        response body. The ProviderError must contain BOTH the code and the
        body's diagnosis (today: only "http 400")."""
        body = CONTEXT_ERROR_BODY

        def _opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request",
                                         None, io.BytesIO(body))
        provider = OpenAICompatProvider(self.cfg, opener=_opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        message = str(ctx.exception)
        self.assertIn("400", message)
        self.assertIn(
            "maximum context length", message,
            "the error response body (the only place a real endpoint states "
            "the context-length diagnosis) was discarded from the "
            "ProviderError message")
        self.assertNotIn(KEY, message)

    def test_non200_without_exception_carries_body(self):
        """The sibling branch: an opener RESPONSE with a non-200 getcode()
        (no exception). Today it raises before ever reading the body."""
        def _opener(request, timeout=None):
            return _FakeResponse(400, CONTEXT_ERROR_BODY)
        provider = OpenAICompatProvider(self.cfg, opener=_opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        message = str(ctx.exception)
        self.assertIn("400", message)
        self.assertIn("maximum context length", message)
        self.assertNotIn(KEY, message)

    def test_error_body_is_size_capped(self):
        """A multi-megabyte error page must not become a multi-megabyte
        exception string: the carried body is capped (head preserved)."""
        huge = b"HEAD-MARKER-r2 " + b"x" * (2 << 20)

        def _opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request",
                                         None, io.BytesIO(huge))
        provider = OpenAICompatProvider(self.cfg, opener=_opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        message = str(ctx.exception)
        self.assertIn("HEAD-MARKER-r2", message)
        self.assertLessEqual(
            len(message), MAX_REASONABLE_ERROR_TEXT_CHARS,
            f"ProviderError text is not size-capped: {len(message)} chars")


class MockEndpointContextStopTests(unittest.TestCase):
    """The item-2 headline: a mock HTTP endpoint returning a realistic
    context-exhaustion body on non-200, through the REAL adapter (no fake
    opener) -- the loop must land in stopped_context (FAIL today: the body
    is discarded, so the run fails generically)."""

    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)
        os.environ[ENV] = KEY
        self.addCleanup(os.environ.pop, ENV, None)
        # Loopback direct, never via any configured system proxy.
        self._saved_proxy_env = {}
        for name in ("no_proxy", "NO_PROXY"):
            self._saved_proxy_env[name] = os.environ.get(name)
            os.environ[name] = "127.0.0.1,localhost"
        self.addCleanup(self._restore_proxy_env)
        try:
            self.server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), _Context400Handler)
        except OSError as e:
            self.skipTest(f"loopback HTTP server unavailable: {e}")
        self.server.port = self.server.server_address[1]
        self.server.requests = []
        thread = threading.Thread(target=self.server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _restore_proxy_env(self):
        for name, value in self._saved_proxy_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _loop(self):
        cfg = ProviderConfig(
            type="openai_compat",
            base_url=f"http://127.0.0.1:{self.server.port}/v1",
            api_key_env=ENV, model="test-model", timeout_s=10.0)
        manifest = Manifest(
            identity=Identity(name="t", role="t", home=Path(self.home)),
            goal="g", policy_path=None, inline_policy={},
            credential_env_vars=(), provider=cfg,
            limits=Limits(max_steps=3))
        # No opener injection: the provider exercises the real urllib path.
        provider = OpenAICompatProvider(cfg)
        return support.build_loop(self.home, [], manifest=manifest,
                                  provider=provider)

    def test_real_endpoint_context_error_lands_in_stopped_context(self):
        loop = self._loop()
        state = loop.run(loop.new_state())
        self.assertEqual(
            state.status, "stopped_context",
            f"a realistic context-exhaustion 400 from a real HTTP endpoint "
            f"did not stop the run in stopped_context (got "
            f"{state.status!r}, failure {state.failure!r}) -- the adapter "
            f"discards the error body, so _is_context_error never fires")
        self.assertIn("context", (state.failure or "").lower())
        self.assertNotIn(KEY, state.failure or "")

    def test_endpoint_was_really_called_over_http(self):
        """Fidelity (passes today): the request actually crossed HTTP with
        the call-time key in the Authorization header -- proving the
        stopped_context assertion above exercises the shipped transport,
        not a stub."""
        loop = self._loop()
        try:
            loop.run(loop.new_state())
        except Exception:
            pass  # the status assertions live above; here we only need the
            # request to have happened
        self.assertTrue(self.server.requests, "the endpoint saw no request")
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["auth"], f"Bearer {KEY}")


if __name__ == "__main__":
    unittest.main()
