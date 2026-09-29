"""The ONLY model-text -> ToolCall path: a strict fenced-block protocol.

The gate sits downstream of this parser; nothing the model writes here can
express "I am already approved" — any extra key makes the block malformed.
"""

import json
from dataclasses import dataclass

from .manifest import Manifest
from .policy import Policy
from .redact import truncate
from .types import ToolCall, ToolResult

TOOL_CALL_OPEN = "<<<TOOL_CALL>>>"
TOOL_CALL_CLOSE = "<<<END_TOOL_CALL>>>"

MAX_BLOCK_CHARS = 262144


@dataclass(frozen=True)
class ParsedCall:
    call: ToolCall | None
    error: str | None


def _flat_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _parse_block(body: str) -> ParsedCall:
    if len(body) > MAX_BLOCK_CHARS:
        return ParsedCall(None, "tool call block too large")
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        return ParsedCall(None, f"invalid JSON in tool call block: {e.msg}")
    if not isinstance(obj, dict):
        return ParsedCall(None, "tool call block must be a JSON object")
    if set(obj) != {"name", "args"}:
        return ParsedCall(None,
                          'tool call block must have exactly the keys "name" and "args"')
    name = obj["name"]
    if not isinstance(name, str) or not name:
        return ParsedCall(None, '"name" must be a non-empty string')
    args = obj["args"]
    if not isinstance(args, dict):
        return ParsedCall(None, '"args" must be an object')
    for value in args.values():
        if isinstance(value, list):
            # Flat lists of scalars are legal (e.g. shell argv); no nesting.
            if any(not _flat_scalar(item) for item in value):
                return ParsedCall(None, '"args" list values must be flat scalars')
        elif not _flat_scalar(value):
            return ParsedCall(
                None, '"args" values must be flat scalars '
                      '(str/int/float/bool/null) or flat lists of scalars')
    return ParsedCall(ToolCall(name=name, args=args), None)


def parse_tool_calls(text: str, max_calls: int = 8) -> list[ParsedCall]:
    """Parse fenced tool-call blocks. Beyond ``max_calls`` blocks, one trailing
    ParsedCall(None, "too many tool call blocks") is emitted. No markers -> []."""
    results: list[ParsedCall] = []
    body_lines: list[str] | None = None
    count = 0
    overflow = False

    for line in text.split("\n"):
        stripped = line.strip()
        if body_lines is None:
            if stripped == TOOL_CALL_OPEN:
                body_lines = []
        elif stripped == TOOL_CALL_CLOSE:
            if count >= max_calls:
                overflow = True
            else:
                results.append(_parse_block("\n".join(body_lines)))
                count += 1
            body_lines = None
        else:
            body_lines.append(line)

    if body_lines is not None:
        if count >= max_calls:
            overflow = True
        else:
            results.append(ParsedCall(None, "unterminated tool call block"))
    if overflow:
        results.append(ParsedCall(None, "too many tool call blocks"))
    return results


def summary(call: ToolCall) -> str:
    """One-line, deterministic rendering used in approver prompts."""
    parts = []
    for key in sorted(call.args):
        value = str(call.args[key])
        if len(value) > 40:
            value = value[:37] + "..."
        parts.append(f"{key}={value}")
    return f"{call.name}(" + ", ".join(parts) + ")"


def format_observation(call: ToolCall, result: ToolResult,
                       truncate_chars: int = 2000) -> str:
    """Deterministic observation fed back to the model as a user message."""
    head = f"TOOL {summary(call)}"
    if result.ok:
        return f"{head}\nOK\n{truncate(result.output, truncate_chars)}"
    code = result.error_code or "unknown"
    return f"{head}\nERROR {code}: {result.error or ''}"


def build_system_prompt(manifest: Manifest, policy: Policy,
                        tool_schemas: list[dict]) -> str:
    lines = [
        f"You are {manifest.identity.name}, {manifest.identity.role}.",
        f"Goal: {manifest.goal}",
        f"Workspace: {manifest.identity.home}",
        "",
        "Tools available (JSON schemas):",
        json.dumps(tool_schemas, indent=2, sort_keys=True),
        "",
        "To call a tool, emit exactly one fenced block per call:",
        TOOL_CALL_OPEN,
        '{"name": "<tool name>", "args": {<flat scalar arguments>}}',
        TOOL_CALL_CLOSE,
        "",
        "Protocol rules:",
        '- the block must be a JSON object with exactly the keys "name" and "args";',
        "- args values must be flat scalars (string, number, boolean, or null);",
        "- there is no way to express pre-approval: a gate will review every call;",
        "- paths are relative to your workspace; escapes are blocked and logged.",
    ]
    return "\n".join(lines)
