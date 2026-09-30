"""ScriptedModel cursor semantics + OpenAICompatProvider via a fake opener:
URL/headers/body, Authorization carries the env value read at call time,
unset-env error, non-200, usage parse/estimate; the key is absent from every
error string."""

import io
import json
import os
import unittest
import urllib.error
from pathlib import Path

import support
from fullstop.manifest import ProviderConfig
from fullstop.provider import (OpenAICompatProvider, ProviderError,
                             ScriptedModel, ScriptedModelExhausted,
                             build_provider)


class _FakeResponse:
    def __init__(self, code, payload):
        self._code = code
        self._payload = json.dumps(payload).encode()

    def getcode(self):
        return self._code

    def read(self, n=-1):  # real response objects accept a size limit
        return self._payload


class ScriptedModelTests(unittest.TestCase):
    def test_pops_in_order_and_tracks_cursor(self):
        model = ScriptedModel(["a", "b"])
        self.assertEqual(model.complete([]).content, "a")
        self.assertEqual(model.cursor, 1)
        self.assertEqual(model.complete([]).content, "b")
        self.assertEqual(model.cursor, 2)

    def test_exhaustion_raises(self):
        model = ScriptedModel(["only"])
        model.complete([])
        with self.assertRaises(ScriptedModelExhausted):
            model.complete([])

    def test_usage_is_estimated(self):
        reply = ScriptedModel(["hello world"]).complete(
            [{"role": "user", "content": "12345678"}])
        self.assertTrue(reply.usage.estimated)
        self.assertEqual(reply.usage.output_tokens, len("hello world") // 4)
        self.assertEqual(reply.usage.input_tokens, 2)
        self.assertEqual(reply.usage.total_tokens,
                         reply.usage.input_tokens + reply.usage.output_tokens)

    def test_from_json_file_with_start_cursor(self):
        with support.temp_dir() as td:
            path = Path(td) / "s.json"
            path.write_text(json.dumps(["a", "b", "c"]), encoding="utf-8")
            model = ScriptedModel.from_json_file(path, start_cursor=2)
            self.assertEqual(model.complete([]).content, "c")

    def test_build_provider_dispatch(self):
        with support.temp_dir() as td:
            path = Path(td) / "s.json"
            path.write_text('["x"]', encoding="utf-8")
            self.assertIsInstance(build_provider(
                ProviderConfig(type="scripted", script_path=path)), ScriptedModel)
            self.assertIsInstance(build_provider(
                ProviderConfig(type="scripted", script_path=path),
                start_cursor=1).cursor, int)
            cfg = ProviderConfig(type="openai_compat", base_url="https://x",
                                 api_key_env="K", model="m")
            self.assertIsInstance(build_provider(cfg), OpenAICompatProvider)
            with self.assertRaises(ProviderError):
                build_provider(ProviderConfig(type="nope"))


class OpenAICompatTests(unittest.TestCase):
    ENV = "FULLSTOP_PROVIDER_TEST_KEY"

    def setUp(self):
        self.marker = "sk-FAKE-provider-key"
        os.environ[self.ENV] = self.marker
        self.addCleanup(os.environ.pop, self.ENV, None)
        self.cfg = ProviderConfig(type="openai_compat",
                                  base_url="https://api.example/v1/",
                                  api_key_env=self.ENV, model="test-model",
                                  timeout_s=5.0)

    def opener(self, captured, code=200, payload=None, exc=None):
        def _opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            if exc is not None:
                raise exc
            return _FakeResponse(code, payload)
        return _opener

    def test_request_shape_and_usage_parse(self):
        captured = {}
        payload = {"choices": [{"message": {"content": "the answer"}}],
                   "usage": {"prompt_tokens": 11, "completion_tokens": 7}}
        provider = OpenAICompatProvider(
            self.cfg, opener=self.opener(captured, payload=payload))
        reply = provider.complete([{"role": "user", "content": "q"}])
        self.assertEqual(reply.content, "the answer")
        self.assertEqual(captured["url"],
                         "https://api.example/v1/chat/completions")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], f"Bearer {self.marker}")
        self.assertEqual(captured["body"]["model"], "test-model")
        self.assertEqual(captured["body"]["temperature"], 0)
        self.assertEqual(captured["body"]["messages"],
                         [{"role": "user", "content": "q"}])
        self.assertEqual(captured["timeout"], 5.0)
        self.assertEqual(reply.usage.input_tokens, 11)
        self.assertEqual(reply.usage.output_tokens, 7)
        self.assertFalse(reply.usage.estimated)
        self.assertAlmostEqual(
            reply.usage.cost_usd(0.5, 2.0),
            11 / 1000 * 0.5 + 7 / 1000 * 2.0)

    def test_usage_estimated_when_missing(self):
        captured = {}
        payload = {"choices": [{"message": {"content": "abc"}}]}
        reply = OpenAICompatProvider(
            self.cfg, opener=self.opener(captured, payload=payload)).complete(
            [{"role": "user", "content": "12345678"}])
        self.assertTrue(reply.usage.estimated)
        self.assertEqual(reply.usage.output_tokens, 0)
        self.assertEqual(reply.usage.input_tokens, 2)

    def test_key_read_at_call_time_not_stored(self):
        captured = {}
        provider = OpenAICompatProvider(
            self.cfg, opener=self.opener(captured,
                                         payload={"choices": [{"message":
                                                      {"content": "x"}}]}))
        os.environ.pop(self.ENV, None)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        self.assertIn(self.ENV, str(ctx.exception))
        self.assertNotIn(self.marker, str(ctx.exception))
        self.assertNotIn("auth", captured)  # the opener was never called
        # key returns at call time
        os.environ[self.ENV] = self.marker
        provider.complete([{"role": "user", "content": "q"}])
        self.assertEqual(captured["auth"], f"Bearer {self.marker}")

    def test_non_200_raises_http_code_without_key(self):
        captured = {}
        provider = OpenAICompatProvider(self.cfg, opener=self.opener(
            captured, code=503, payload={}))
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        # v0.1.2 (FIXLIST2 item 2): the code is carried AND, when the
        # endpoint sent one, the body (this fixture answers "{}").
        self.assertIn("http 503", str(ctx.exception))
        self.assertNotIn(self.marker, str(ctx.exception))

    def test_http_error_object_raises_http_code(self):
        def _opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 500, "boom", None,
                                         io.BytesIO(b""))
        provider = OpenAICompatProvider(self.cfg, opener=_opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        self.assertEqual(str(ctx.exception), "http 500")

    def test_url_error_names_type_without_key(self):
        def _opener(request, timeout=None):
            raise urllib.error.URLError("no route to host")
        provider = OpenAICompatProvider(self.cfg, opener=_opener)
        with self.assertRaises(ProviderError) as ctx:
            provider.complete([{"role": "user", "content": "q"}])
        self.assertIn("URLError", str(ctx.exception))
        self.assertNotIn(self.marker, str(ctx.exception))

    def test_malformed_response_raises(self):
        provider = OpenAICompatProvider(self.cfg, opener=self.opener(
            {}, payload={"choices": []}))
        with self.assertRaises(ProviderError):
            provider.complete([{"role": "user", "content": "q"}])


if __name__ == "__main__":
    unittest.main()
