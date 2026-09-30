"""Executable resolution for the shell gate and tool (FIXLIST item 7).

The rule: the program an allowlist entry names and the program a call
actually runs are compared as RESOLVED ABSOLUTE REAL PATHS, looked up
strictly against PATH — never against the process cwd. On Windows,
CreateProcess resolves a bare argv0 against the child's cwd BEFORE PATH, so
a workspace-planted binary would otherwise shadow the allowlisted program;
and a ``.bat``/``.cmd`` argv0 makes CreateProcess route through ``cmd.exe``,
which re-parses the argument string (the BatBadBut class, CVE-2024-24576).

Consequences, deliberately:
- bare names resolve to ``<PATH-dir>/<name>.exe`` on Windows (PATHEXT is
  pinned to ``.exe``; script/batch extensions never resolve);
- a name spelled with a path separator must be absolute — cwd-relative
  spellings refuse to resolve (fail closed);
- anything that does not resolve is simply not pre-approvable.
"""

import os
from pathlib import Path

IS_WINDOWS = os.name == "nt"
_EXE = ".exe"
# Script/batch interpreters refused outright, on every platform: executing
# one hands the argument string to a re-parsing shell.
SCRIPT_SUFFIXES = (
    ".bat", ".cmd", ".com", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msc", ".scr", ".sh", ".bash", ".csh", ".ksh", ".fish",
)


def is_script_target(name: str) -> bool:
    low = name.casefold()
    return any(low.endswith(s) for s in SCRIPT_SUFFIXES)


def resolve_program(name: str) -> Path | None:
    """Resolve a program spelling to an absolute real path, or None.

    PATH-only lookup for bare names; absolute spellings used as-is (and must
    be a real file). Never resolves relative spellings or script targets.
    """
    if not isinstance(name, str):
        return None
    n = name.strip()
    if not n or "\x00" in n:
        return None
    if is_script_target(n):
        return None
    has_sep = "/" in n or "\\" in n
    if IS_WINDOWS:
        absolute = len(n) > 1 and n[1] == ":" or n.startswith("\\\\")
        if has_sep or absolute:
            p = Path(n)
            if not p.is_absolute():
                return None  # cwd-relative spelling: fail closed
            if p.suffix.casefold() != _EXE:
                return None  # only .exe targets resolve on Windows
            real = Path(os.path.realpath(p))
            return real if real.is_file() else None
        if n.casefold().endswith(_EXE):
            base = n  # spelled with .exe: look it up by full name
        elif "." in n:
            return None  # some other extension: not resolvable
        else:
            base = n + _EXE
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            directory = directory.strip()
            if not directory:
                continue
            candidate = Path(directory) / base
            if candidate.is_file():
                return Path(os.path.realpath(candidate))
        return None
    # POSIX
    if has_sep or n.startswith("/"):
        p = Path(n)
        if not p.is_absolute():
            return None
        real = Path(os.path.realpath(p))
        return real if real.is_file() else None
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = directory.strip()
        if not directory:
            continue
        candidate = Path(directory) / n
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return Path(os.path.realpath(candidate))
    return None


def programs_match(entry_program: str, argv0: str) -> bool:
    """True iff both spellings resolve to the same absolute real program."""
    a = resolve_program(entry_program)
    b = resolve_program(argv0)
    if a is None or b is None:
        return False
    return os.path.normcase(str(a)) == os.path.normcase(str(b))
