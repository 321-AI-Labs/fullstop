"""Secret scrubbing and truncation — enforced at every write layer."""

import os
from typing import Iterable


class Redactor:
    """Replaces known secret values with ``[REDACTED:<NAME>]`` markers.

    Longer values are replaced first so that a short secret that is a prefix
    of a longer one cannot shield it.
    """

    def __init__(self, secrets: dict[str, str]) -> None:
        self._pairs = sorted(
            ((name, value) for name, value in secrets.items() if value),
            key=lambda nv: (-len(nv[1]), nv[0]),
        )

    def scrub(self, text: str) -> str:
        for name, value in self._pairs:
            if value and value in text:
                text = text.replace(value, f"[REDACTED:{name}]")
        return text

    @classmethod
    def from_env(cls, names: Iterable[str]) -> "Redactor":
        """Build a Redactor from env var NAMES; unset names silently skipped."""
        secrets: dict[str, str] = {}
        for name in names:
            value = os.environ.get(name)
            if value:
                secrets[name] = value
        return cls(secrets)


def truncate(text: str, limit: int) -> str:
    """Cap ``text`` at ``limit`` characters, noting the elision."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
