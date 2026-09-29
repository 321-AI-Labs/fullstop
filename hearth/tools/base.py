"""Tool protocol + registry. The registry is where gate tokens are enforced:
no tool executes without a Gate-minted ALLOW decision for THAT exact call."""

from typing import Callable, Protocol

from ..sandbox import SandboxError
from ..types import GateDecision, ToolCall, ToolResult


class ToolError(RuntimeError):
    pass


class Tool(Protocol):
    name: str

    def schema(self) -> dict: ...

    def execute(self, call: ToolCall) -> ToolResult: ...


def _deny_all(call: ToolCall, decision: GateDecision) -> None:
    raise PermissionError(
        f"registry has no gate verifier; refusing to execute {call.name}")


class ToolRegistry:
    def __init__(
        self,
        verify: Callable[[ToolCall, GateDecision], None] | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._verify: Callable[[ToolCall, GateDecision], None] = verify or _deny_all

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def schemas(self) -> list[dict]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def execute(self, call: ToolCall, decision: GateDecision) -> ToolResult:
        # 1. A forged or absent decision NEVER executes; PermissionError
        #    propagates to the loop, which treats it as a failed run.
        self._verify(call, decision)
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, output="",
                              error=f"unknown tool: {call.name}",
                              error_code="unknown_tool")
        try:
            return tool.execute(call)
        except SandboxError as e:
            # 2. Sandbox violations are caught FIRST and carry the vector class.
            return ToolResult(ok=False, output="", error=str(e),
                              error_code="sandbox_escape")
        except Exception as e:  # 3. The loop never crashes on a tool.
            return ToolResult(ok=False, output="",
                              error=f"{type(e).__name__}: {e}",
                              error_code="io_error")
