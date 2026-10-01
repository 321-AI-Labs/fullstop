"""The single filesystem chokepoint: every user-supplied path dies here first.

Ordered algorithm (normative, from the approved plan):

1. must be a non-empty str without NUL bytes;
2. backslashes become forward slashes;
3. reject POSIX-absolute (leading ``/``), Windows drive forms
   (``[A-Za-z]:.*``), and ANY remaining ``:`` (covers UNC-after-slash-replace
   leftovers, drive-relative paths like ``C:foo``, alternate data streams like
   ``f.txt:evil``, and URL-ish spellings);
4. reject any ``/``-separated component equal to ``..``;
5. realpath both the candidate and the root;
6. containment via ``os.path.commonpath`` (cross-drive ``ValueError`` counts
   as a rejection);
7. return the realpath.
"""

import os
import re
from pathlib import Path

_DRIVE_RE = re.compile(r"[A-Za-z]:.*")


class SandboxError(PermissionError):
    """Raised when a path attempts to leave the workspace sandbox.

    The message names the vector class; it feeds ``sandbox_block`` events.
    """


def resolve_in_sandbox(root: Path, user_path: str) -> Path:
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        raise SandboxError(
            "invalid path (empty, non-string, or contains NUL): "
            f"{user_path!r}"
        )
    p = user_path.replace("\\", "/")
    if p.startswith("/"):
        raise SandboxError(f"absolute path rejected: {user_path!r}")
    if _DRIVE_RE.fullmatch(p) or ":" in p:
        raise SandboxError(
            "colon path rejected (drive, UNC, alternate-data-stream, or URL): "
            f"{user_path!r}"
        )
    if any(part == ".." for part in p.split("/")):
        raise SandboxError(f"parent traversal (..) rejected: {user_path!r}")
    root_real = Path(os.path.realpath(root))
    real = Path(os.path.realpath(root / p))
    try:
        contained = os.path.commonpath([str(root_real), str(real)]) == str(root_real)
    except ValueError:
        contained = False
    if not contained:
        raise SandboxError(
            f"path resolves outside the workspace sandbox: {user_path!r}"
        )
    return real


def rel_posix(root: Path, target: Path) -> str:
    """Forward-slash relative path of ``target`` inside ``root``.

    Callers casefold only for matching, never for display decisions.
    """
    return Path(os.path.relpath(target, root)).as_posix()


def sandbox_rel(root: Path, target: Path) -> str:
    """rel_posix of ``target`` against the REALPATH'D root spelling.

    ``resolve_in_sandbox`` realpaths both sides for containment but returns
    only the target. If the caller then takes the rel against the RAW root
    spelling and that spelling is an alias (GitHub's Windows runners expose
    TEMP as ``C:\\Users\\RUNNER~1\\...``, a symlinked root is the same
    shape), every rel crawls out as ``../../../...`` garbage and equality or
    alias checks built on it misfire. Roots fed here must therefore be
    realpath'd first — exactly once, here.
    """
    return rel_posix(Path(os.path.realpath(root)), target)
