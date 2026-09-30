"""Tool registry factory: registers all seven v0.1 tools.

Threads the fetch seam (tests/smoke inject a fake; the CLI passes None ->
default real fetch) and the credential env scrubbing for shell children.
"""

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
