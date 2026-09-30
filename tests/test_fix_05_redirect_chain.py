"""FIXLIST item 5 (must-fix): web_fetch redirect bypass — the adversarial
redirect chain test.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect (memo
defect 2): the domain predicate runs on the ORIGINAL url only while urllib
auto-follows 3xx, so an approved fetch that redirects to a denylisted (or
non-allowlisted) host fetches it under a human approval signature.

Frozen contract (memo endorsement: "per-hop full-predicate re-validation"):

- every Location hop is re-validated against the COMPLETE domain
  allow/deny predicate (denylist hop -> deny; with an allowlist configured,
  a hop to a host not on the allowlist -> deny — not denylist-only
  checking, per turn-05 critique 4);
- a failing hop denies the fetch BEFORE the hop is requested;
- a redirect chain whose every hop satisfies the policy still fetches
  (no overblocking).

The chain is served by a loopback-only stdlib HTTP server (bound to
127.0.0.1, ephemeral port). "127.0.0.1" (allowlisted below, fast to
connect) and "localhost" (the denied/unlisted hop host) are distinct HOST
STRINGS for the policy while both reachable locally — zero external
network. The tool under test uses its real default_fetch (real urllib
redirect-following); nothing is faked at the fetch seam. If the loopback
socket cannot be bound, the tests skip loudly rather than silently pass.
"""

import http.server
import threading
import unittest
from pathlib import Path

import support
from fullstop.policy import Policy, WebPolicy
from fullstop.types import Action, ToolCall

EVIL_BODY = b"evil-content-should-never-be-fetched"
GOOD_BODY = b"good-content"


class _ChainHandler(http.server.BaseHTTPRequestHandler):
    server_version = "fullstop-fixtest"

    def do_GET(self):  # noqa: N802 (http.server API)
        server = self.server
        server.requests.append(f"{self.headers.get('Host', '')}{self.path}")
        if self.path == "/start":  # hop 1 -> 127.0.0.1 /mid (allowed host)
            self._redirect(f"http://127.0.0.1:{server.port}/mid")
        elif self.path == "/mid":  # hop 2 -> localhost /evil (denied host)
            self._redirect(f"http://localhost:{server.port}/evil")
        elif self.path == "/start2":  # single hop straight to localhost
            self._redirect(f"http://localhost:{server.port}/evil")
        elif self.path == "/localok":  # -> 127.0.0.1 /ok (all hops allowed)
            self._redirect(f"http://127.0.0.1:{server.port}/ok")
        elif self.path == "/evil":
            self._body(EVIL_BODY)
        else:  # /ok
            self._body(GOOD_BODY)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


class RedirectChainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        try:
            self.server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), _ChainHandler)
        except OSError as e:
            self.skipTest(f"loopback HTTP server unavailable: {e}")
        self.server.port = self.server.server_address[1]
        self.server.requests = []
        thread = threading.Thread(target=self.server.serve_forever,
                                  daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _harness(self, web: WebPolicy):
        return support.gate_harness(self.tmp, policy=Policy(web=web))

    def test_denylisted_redirect_hop_denies_the_fetch(self):
        """/start (127.0.0.1, allowlisted) -> /mid (127.0.0.1) -> /evil
        (localhost, DENYLISTED). Today urllib silently follows to the denied
        host and returns its body under the original approval."""
        h = self._harness(WebPolicy(allow_domains=("127.0.0.1",),
                                    deny_domains=("localhost",)))
        url = f"http://127.0.0.1:{self.server.port}/start"
        decision = h.gate.decide(ToolCall("web_fetch", {"url": url}))
        self.assertEqual(decision.action, Action.ALLOW)  # original URL is fine
        result = h.registry.execute(ToolCall("web_fetch", {"url": url}),
                                    decision)
        self.assertFalse(
            result.ok,
            f"a denylisted-host redirect hop was fetched: {result.output!r}")
        self.assertEqual(result.error_code, "domain_denied", result.error)
        self.assertNotIn(
            f"localhost:{self.server.port}/evil", self.server.requests,
            "the denied hop must not even be REQUESTED")

    def test_allowlist_gap_redirect_hop_denies_the_fetch(self):
        """Full-predicate re-validation, not denylist-only (turn-05): with an
        allowlist configured, a hop to a host NOT on the allowlist (and not
        on the denylist) was never approved by anyone -> deny."""
        h = self._harness(WebPolicy(allow_domains=("127.0.0.1",),
                                    deny_domains=()))
        url = f"http://127.0.0.1:{self.server.port}/start2"
        decision = h.gate.decide(ToolCall("web_fetch", {"url": url}))
        self.assertEqual(decision.action, Action.ALLOW)
        result = h.registry.execute(ToolCall("web_fetch", {"url": url}),
                                    decision)
        self.assertFalse(result.ok, result.output)
        self.assertEqual(result.error_code, "domain_denied", result.error)
        self.assertNotIn(f"localhost:{self.server.port}/evil",
                         self.server.requests)

    def test_fully_allowed_chain_still_fetches(self):
        """GUARD against overblocking (passes today, must keep passing): a
        redirect whose every hop satisfies the policy completes."""
        h = self._harness(WebPolicy(allow_domains=("127.0.0.1",),
                                    deny_domains=("localhost",)))
        url = f"http://127.0.0.1:{self.server.port}/localok"
        result = h.execute(ToolCall("web_fetch", {"url": url}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("good-content", result.output)


if __name__ == "__main__":
    unittest.main()
