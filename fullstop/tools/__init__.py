"""Tool registry factory: registers all seven v0.1 tools.

Threads the fetch seam (tests/smoke inject a fake; the CLI passes None ->
default real fetch) and the credential env scrubbing for shell children.
"""

import os
from pathlib import Path
from typing import Callable

from ..policy import Policy
from ..redact import Redactor
from ..types import GateDecision, ToolCall
from .base import ToolRegistry
from .browser import BrowserTool
from .file import FileListTool, FileReadTool, FileWriteTool
from .note import NoteTool
from .shell import ShellTool
from .web import WebFetchTool


def build_registry(
    sandbox_root: Path,
    policy: Policy,
    redactor: Redactor,
    verify: Callable[[ToolCall, GateDecision], None],
    fetch: Callable[[str, float], tuple[int, str, bytes]] | None = None,
    scrub_env: tuple[str, ...] = (),
) -> ToolRegistry:
    # Canonicalize the root ONCE, here at the composition root: every
    # filesystem tool stores this spelling and compares rel_posix(root,
    # resolved) against it, while resolve_in_sandbox returns
    # realpath-normalized targets. A root spelled through an 8.3 short name
    # (GitHub Windows runners: C:\Users\RUNNER~1) or a junction/symlink
    # differs from its realpath, so every write-equality check would fail
    # closed with "path resolves elsewhere in workspace" (the Windows CI
    # red of 2026-09-30..2026-10-19). User-supplied PATHS still fail closed
    # on unusual spellings; the root is harness config, not user input.
    sandbox_root = Path(os.path.realpath(sandbox_root))
    registry = ToolRegistry(verify=verify)
    registry.register(FileReadTool(sandbox_root, redactor, policy))
    registry.register(FileWriteTool(sandbox_root, redactor, policy))
    registry.register(FileListTool(sandbox_root, redactor, policy))
    registry.register(NoteTool(sandbox_root, redactor))
    registry.register(ShellTool(policy.shell, sandbox_root, redactor,
                                scrub_env=scrub_env))
    if fetch is not None:
        registry.register(WebFetchTool(policy.web, redactor, fetch=fetch))
    else:
        registry.register(WebFetchTool(policy.web, redactor))
    registry.register(BrowserTool())
    return registry
