"""DoD#4: a fake key value provably never appears in any file the runtime
writes — prose echo, file content, note text, and the shell environment are
all made to carry it; the redaction layers must scrub every one, and the
provider must carry it ONLY in the Authorization header."""

import io
import json
import os
import unittest
import urllib.error
import uuid
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover
from fullstop.manifest import ProviderConfig
from fullstop.policy import Policy, ShellPolicy
from fullstop.provider import OpenAICompatProvider, ProviderError
from fullstop.redact import Redactor

ENV_NAME = "FULLSTOP_FAKE_KEY"


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        # 24-char marker: short enough to survive summary() truncation, so the
        # FULL value really reaches every write layer and must be scrubbed.
        self.marker = "sk-FAKE-" + uuid.uuid4().hex[:16]
        os.environ[ENV_NAME] = self.marker
        self.addCleanup(os.environ.pop, ENV_NAME, None)
        self.home = support.make_home(self.tmp)

    def test_marker_never_written_anywhere(self):
        policy = Policy(
            write_preapproved=("memory.md",),
            shell=ShellPolicy(allow=(sys_exe(),)),
        )
        replies = [
            # Prose echo + note smuggling + file_write smuggling.
            f"The key is {self.marker}, watch me try.\n"
            + support.call_block("note", {"text": f"secret {self.marker}"})
            + "\n" + support.call_block("file_write",
                                        {"path": "leak.md",
                                         "content": f"leak {self.marker}"}),
            # Allowlisted child dumping its whole environment.
            support.call_block("shell", {
                "argv": [sys_exe(), "-c", "import os; print(os.environ)"]}),
            "done",
        ]
        loop = support.build_loop(
            self.home, replies, policy=policy,
            approver=ScriptedApprover([True]),  # approve the leak.md write
            manifest=support.script_manifest(
                self.home, self.home / ".fullstop" / "script.json",
                credentials=[ENV_NAME]),
            redactor=Redactor.from_env([ENV_NAME]))
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed")

        # Byte-grep EVERY runtime-written file under the workspace.
        written = [p for p in self.home.rglob("*") if p.is_file()]
        self.assertGreaterEqual(len(written), 3)
        for path in written:
            data = path.read_bytes()
            self.assertNotIn(self.marker.encode(), data,
                             f"marker leaked into {path}")
        log_text = (self.home / ".fullstop" / "activity.jsonl").read_text(
            encoding="utf-8")
        self.assertIn(f"[REDACTED:{ENV_NAME}]", log_text)
        # The write layers scrubbed content too.
        self.assertIn(f"[REDACTED:{ENV_NAME}]",
                      (self.home / "leak.md").read_text(encoding="utf-8"))
        self.assertIn(f"[REDACTED:{ENV_NAME}]",
                      (self.home / "memory.md").read_text(encoding="utf-8"))
        # The checkpoint scrubbed messages.
        state_text = (self.home / ".fullstop" / "state.json").read_text(
            encoding="utf-8")
        self.assertNotIn(self.marker, state_text)

        # The shell child never even SAW the credential name.
        results = support.tool_results(support.read_events(self.home))
        shell_result = [r for r in results if r["tool"] == "shell"][0]
        self.assertNotIn(ENV_NAME, shell_result["output"])
        self.assertNotIn(self.marker, shell_result["output"])

    def test_provider_value_lives_only_in_authorization_header(self):
        captured = {}

        def fake_opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = request.data.decode("utf-8")
            return _FakeResponse(200, {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            })

        cfg = ProviderConfig(type="openai_compat",
                             base_url="https://api.example/v1/",
                             api_key_env=ENV_NAME, model="m")
        provider = OpenAICompatProvider(cfg, opener=fake_opener)
        reply = provider.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(reply.content, "ok")
        self.assertEqual(captured["auth"], f"Bearer {self.marker}")
        self.assertNotIn(self.marker, captured["url"])
        self.assertNotIn(self.marker, captured["body"])

        # Key absent from every error path.
        os.environ.pop(ENV_NAME, None)
        with self.assertRaises(ProviderError) as ctx:
            OpenAICompatProvider(cfg, opener=fake_opener).complete(
                [{"role": "user", "content": "hi"}])
        self.assertIn(ENV_NAME, str(ctx.exception))
        self.assertNotIn(self.marker, str(ctx.exception))

        os.environ[ENV_NAME] = self.marker
        err_opener = lambda request, timeout=None: _raise(
            urllib.error.HTTPError(request.full_url, 500, "boom", None,
                                   io.BytesIO(b"")))
        with self.assertRaises(ProviderError) as ctx:
            OpenAICompatProvider(cfg, opener=err_opener).complete(
                [{"role": "user", "content": "hi"}])
        self.assertEqual(str(ctx.exception), "http 500")
        self.assertNotIn(self.marker, str(ctx.exception))

        net_opener = lambda request, timeout=None: _raise(
            urllib.error.URLError("no route"))
        with self.assertRaises(ProviderError) as ctx:
            OpenAICompatProvider(cfg, opener=net_opener).complete(
                [{"role": "user", "content": "hi"}])
        self.assertIn("URLError", str(ctx.exception))
        self.assertNotIn(self.marker, str(ctx.exception))


class _FakeResponse:
    def __init__(self, code, payload):
        self._code = code
        self._payload = json.dumps(payload).encode()

    def getcode(self):
        return self._code

    def read(self):
        return self._payload


def _raise(exc):
    raise exc


def sys_exe() -> str:
    import sys
    return sys.executable


if __name__ == "__main__":
    unittest.main()
