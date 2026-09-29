"""Policy loading and glob predicates (default-deny posture).

DOCS LAW (from the approved plan): policy docs and the README state that
``note`` is a WRITE gated exactly like ``file_write`` against NOTE_FILENAME
("memory.md"). Operators who want frictionless memory put "memory.md" in
``write_preapproved`` — both shipped example policies do.
"""

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ShellPolicy:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    timeout_s: float = 30.0


@dataclass(frozen=True)
class WebPolicy:
    allow_domains: tuple[str, ...] = ()
    deny_domains: tuple[str, ...] = ()
    timeout_s: float = 10.0
    max_bytes: int = 1_048_576


@dataclass(frozen=True)
class Policy:
    protected_paths: tuple[str, ...] = ()
    write_preapproved: tuple[str, ...] = ()
    shell: ShellPolicy = field(default_factory=ShellPolicy)
    web: WebPolicy = field(default_factory=WebPolicy)

    # Non-removable: the agent must never tamper its own state or activity log.
    BUILTIN_PROTECTED: ClassVar[tuple[str, ...]] = (".hearth/**",)

    def is_protected(self, rel_posix: str) -> bool:
        rel = rel_posix.casefold()
        for pat in self.BUILTIN_PROTECTED + self.protected_paths:
            if fnmatch.fnmatch(rel, pat.casefold()):
                return True
        return False

    def is_write_preapproved(self, rel_posix: str) -> bool:
        rel = rel_posix.casefold()
        for pat in self.write_preapproved:
            if fnmatch.fnmatch(rel, pat.casefold()):
                return True
        return False


def _str_list(value, label: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        problems.append(f"- {label} must be a list of non-empty strings")
        return ()
    return tuple(value)


def _num(value, label: str, problems: list[str], default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        problems.append(f"- {label} must be a positive number")
        return default
    return float(value)


def policy_from_dict(data: dict) -> Policy:
    if not isinstance(data, dict):
        raise PolicyError("- policy must be a JSON object")
    problems: list[str] = []

    known_top = {"protected_paths", "write_preapproved", "shell", "web"}
    for key in sorted(set(data) - known_top):
        problems.append(f"- unknown policy key: {key}")

    protected = _str_list(data.get("protected_paths", []), "protected_paths", problems)
    preapproved = _str_list(data.get("write_preapproved", []), "write_preapproved", problems)

    shell_data = data.get("shell", {})
    if not isinstance(shell_data, dict):
        problems.append("- shell must be an object")
        shell_data = {}
    else:
        for key in sorted(set(shell_data) - {"allow", "deny", "timeout_s"}):
            problems.append(f"- unknown shell key: {key}")
    shell = ShellPolicy(
        allow=_str_list(shell_data.get("allow", []), "shell.allow", problems),
        deny=_str_list(shell_data.get("deny", []), "shell.deny", problems),
        timeout_s=_num(shell_data.get("timeout_s"), "shell.timeout_s", problems, 30.0),
    )

    web_data = data.get("web", {})
    if not isinstance(web_data, dict):
        problems.append("- web must be an object")
        web_data = {}
    else:
        for key in sorted(set(web_data) - {"allow_domains", "deny_domains",
                                           "timeout_s", "max_bytes"}):
            problems.append(f"- unknown web key: {key}")
    max_bytes = web_data.get("max_bytes")
    if max_bytes is not None and (not isinstance(max_bytes, int)
                                  or isinstance(max_bytes, bool) or max_bytes < 1):
        problems.append("- web.max_bytes must be a positive integer")
        max_bytes = 1_048_576
    web = WebPolicy(
        allow_domains=_str_list(web_data.get("allow_domains", []), "web.allow_domains", problems),
        deny_domains=_str_list(web_data.get("deny_domains", []), "web.deny_domains", problems),
        timeout_s=_num(web_data.get("timeout_s"), "web.timeout_s", problems, 10.0),
        max_bytes=int(max_bytes) if max_bytes is not None else 1_048_576,
    )

    if problems:
        raise PolicyError("invalid policy:\n" + "\n".join(problems))
    return Policy(protected_paths=protected, write_preapproved=preapproved,
                  shell=shell, web=web)


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise PolicyError(f"cannot read policy {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise PolicyError(f"policy {path} is not valid JSON: {e}") from e
    return policy_from_dict(data)
