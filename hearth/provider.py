"""Model providers: the OpenAI-compatible adapter and the ScriptedModel mock.

DEVIATION (stated): CHARTER.md:15 says ScriptedModel "ships in the test suite";
it ships in the PACKAGE because the keyless quickstart (CHARTER.md:20) and the
smoke script need it. Tests import it from here. Named in the README so a DoD
reader sees the deviation is deliberate.

The API key is read from the environment AT CALL TIME, is never stored on the
provider instance, and never appears in any exception text.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .manifest import ProviderConfig


class ProviderError(RuntimeError):
    pass


class ScriptedModelExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost_usd(self, usd_per_1k_input: float, usd_per_1k_output: float) -> float:
        return (self.input_tokens / 1000.0 * usd_per_1k_input
                + self.output_tokens / 1000.0 * usd_per_1k_output)


@dataclass(frozen=True)
class ModelReply:
    content: str
    usage: Usage


class ModelProvider(Protocol):
    def complete(self, messages: list[dict]) -> ModelReply: ...


class OpenAICompatProvider:
    def __init__(self, cfg: ProviderConfig,
                 opener: Callable = urllib.request.urlopen) -> None:
        if cfg.type != "openai_compat":
            raise ProviderError(f"provider type {cfg.type!r} is not openai_compat")
        self._cfg = cfg
        self._opener = opener

    def complete(self, messages: list[dict]) -> ModelReply:
        import os
        cfg = self._cfg
        key = os.environ.get(cfg.api_key_env or "")
        if not key:
            raise ProviderError(f"env var {cfg.api_key_env} not set")
        url = (cfg.base_url or "").rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": cfg.model,
            "messages": messages,
            "temperature": 0,
        }).encode()
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        try:
            response = self._opener(request, timeout=cfg.timeout_s)
            code = response.getcode() if hasattr(response, "getcode") else 200
            if code != 200:
                raise ProviderError(f"http {code}")
            payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"http {e.code}") from e
        except ProviderError:
            raise
        except Exception as e:  # URLError, socket errors, bad JSON, ...
            raise ProviderError(f"{type(e).__name__}: {e}") from e
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"malformed provider response: {e}") from e
        usage_raw = payload.get("usage") or {}
        if (isinstance(usage_raw, dict)
                and isinstance(usage_raw.get("prompt_tokens"), int)
                and isinstance(usage_raw.get("completion_tokens"), int)):
            usage = Usage(input_tokens=usage_raw["prompt_tokens"],
                          output_tokens=usage_raw["completion_tokens"],
                          estimated=False)
        else:
            usage = Usage(
                input_tokens=sum(len(str(m.get("content", ""))) for m in messages) // 4,
                output_tokens=len(content) // 4,
                estimated=True,
            )
        return ModelReply(content=content, usage=usage)


class ScriptedModel:
    """Pops scripted replies in order; raises ScriptedModelExhausted when spent."""

    def __init__(self, replies: list[str], start_cursor: int = 0) -> None:
        self._replies = list(replies)
        self.cursor = start_cursor

    @classmethod
    def from_json_file(cls, path: str | Path,
                       start_cursor: int = 0) -> "ScriptedModel":
        try:
            replies = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as e:
            raise ProviderError(f"cannot read script {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ProviderError(f"script {path} is not valid JSON: {e}") from e
        if not isinstance(replies, list) or not all(isinstance(r, str) for r in replies):
            raise ProviderError(f"script {path} must be a JSON list of strings")
        return cls(replies, start_cursor=start_cursor)

    def complete(self, messages: list[dict]) -> ModelReply:
        if self.cursor >= len(self._replies):
            raise ScriptedModelExhausted(
                f"script exhausted at cursor {self.cursor}")
        content = self._replies[self.cursor]
        self.cursor += 1
        usage = Usage(
            input_tokens=sum(len(str(m.get("content", ""))) for m in messages) // 4,
            output_tokens=len(content) // 4,
            estimated=True,
        )
        return ModelReply(content=content, usage=usage)


def build_provider(cfg: ProviderConfig, start_cursor: int = 0) -> ModelProvider:
    if cfg.type == "scripted":
        if cfg.script_path is None:
            raise ProviderError("scripted provider requires script_path")
        return ScriptedModel.from_json_file(cfg.script_path, start_cursor=start_cursor)
    if cfg.type == "openai_compat":
        return OpenAICompatProvider(cfg)
    raise ProviderError(f"unknown provider type: {cfg.type}")
