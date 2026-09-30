"""Secret scrubbing and truncation — enforced at every write layer."""

import os
from typing import Iterable


class Redactor:
    """Replaces known secret values with ``[REDACTED:<NAME>]`` markers.

    Literal pairs (``Redactor({...})``) are scrubbed as given. Env-sourced
    names (``from_env``) are read AT CALL TIME (FIXLIST item 11): a
    credential that appears or rotates mid-run is scrubbed immediately,
    matching the provider's call-time key reads. Longer values are replaced
    first so a short secret that is a prefix of a longer one cannot shield
    it.
    """

    def __init__(self, secrets: dict[str, str],
                 env_names: Iterable[str] = ()) -> None:
        self._literals = tuple(sorted(
            ((name, value) for name, value in secrets.items() if value),
            key=lambda nv: (-len(nv[1]), nv[0]),
        ))
        self._env_names = tuple(name for name in env_names if name)

    def _effective(self) -> tuple[tuple[str, str], ...]:
        pairs = dict(self._literals)
        for name in self._env_names:
            value = os.environ.get(name)
            if value:
                pairs[name] = value
        return tuple(sorted(pairs.items(),
                            key=lambda nv: (-len(nv[1]), nv[0])))

    def scrub(self, text: str) -> str:
        for name, value in self._effective():
            if value and value in text:
                text = text.replace(value, f"[REDACTED:{name}]")
        return text

    @classmethod
    def from_env(cls, names: Iterable[str]) -> "Redactor":
        """Build a Redactor from env var NAMES; values are read at scrub
        time, unset names silently skipped."""
        return cls({}, env_names=names)


def truncate(text: str, limit: int) -> str:
    """Cap ``text`` at ``limit`` characters, noting the elision."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
