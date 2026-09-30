"""BrowserTool stub: the interface is defined, execution is not (v0.1 non-goal)."""

from ..types import ToolCall, ToolResult
from .base import Tool


class BrowserTool(Tool):
    name = "browser"

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": ("Browser automation (PLANNED, not implemented in "
                            "v0.1). Intended args: navigate(url), click(selector), "
                            "read(selector) — documented for forward-compatibility."),
            "args": {"action": "planned: 'navigate' | 'click' | 'read'",
                     "url": "planned: string",
                     "selector": "planned: string"},
        }

    def execute(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            ok=False, output="",
            error="browser tool is defined but not implemented in v0.1",
            error_code="not_implemented")
