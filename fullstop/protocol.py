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


def _close_outside_strings(text: str, start: int) -> int:
    """Index of the first TOOL_CALL_CLOSE at or after ``start`` that is not
    inside a JSON string literal, or -1. Markers embedded inside argument
    string values (e.g. a forged fence planted in a path) must stay inert
    parsing-wise while still being delivered verbatim in observations."""
    in_string = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif text.startswith(TOOL_CALL_CLOSE, i):
            return i
        i += 1
    return -1


def parse_tool_calls(text: str, max_calls: int = 8) -> list[ParsedCall]:
    """Parse fenced tool-call blocks. Beyond ``max_calls`` blocks, one trailing
    ParsedCall(None, "too many tool call blocks") is emitted. No markers -> [].

    v0.1.2 (live-check bug 2): fences are recognized anywhere in the reply,
    not as whole lines — real models sometimes glue <<<TOOL_CALL>>> directly
    onto prose with no newline, and the line-anchored match silently read
    those replies as "no calls" (a false completion). Markers inside JSON
    string values remain inert (see _close_outside_strings)."""
    results: list[ParsedCall] = []
    count = 0
    overflow = False
    i = 0
    while True:
        open_at = text.find(TOOL_CALL_OPEN, i)
        if open_at < 0:
            break
        body_start = open_at + len(TOOL_CALL_OPEN)
        close_at = _close_outside_strings(text, body_start)
        if count >= max_calls:
            overflow = True
            if close_at < 0:
                break
            i = close_at + len(TOOL_CALL_CLOSE)
            continue
        if close_at < 0:
            results.append(ParsedCall(None, "unterminated tool call block"))
            break
        results.append(_parse_block(text[body_start:close_at]))
        count += 1
        i = close_at + len(TOOL_CALL_CLOSE)
    if overflow:
        results.append(ParsedCall(None, "too many tool call blocks"))
    return results


def summary(call: ToolCall) -> str:
    """One-line, deterministic rendering for MODEL-FACING observations
    (40-char per-arg cap keeps the model's context lean)."""
    parts = []
    for key in sorted(call.args):
        value = str(call.args[key])
        if len(value) > 40:
            value = value[:37] + "..."
        parts.append(f"{key}={value}")
    return f"{call.name}(" + ", ".join(parts) + ")"


# v0.1.1 (FIXLIST item 6): the render the HUMAN decides on. Generous caps,
# clearly marked elision — the approver must never approve a payload they
# cannot see, and two payloads diverging past char 40 must be
# distinguishable (the truncation-collision kill).
RENDER_PER_ARG_CHARS = 4000
RENDER_TOTAL_CHARS = 8000


def render_request(call: ToolCall,
                   per_arg: int = RENDER_PER_ARG_CHARS,
                   total: int = RENDER_TOTAL_CHARS) -> str:
    """Faithful, deterministic rendering of a request for approver prompts
    and for re-displaying persisted pending requests on replay."""
    parts = []
    for key in sorted(call.args):
        raw = call.args[key]
        value = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)
        if len(value) > per_arg:
            value = value[:per_arg] + f"...[truncated {len(value) - per_arg} chars]"
        parts.append(f"{key}={value}")
    line = f"{call.name}(" + ", ".join(parts) + ")"
    if len(line) > total:
        line = (line[:total]
                + f" ...[request truncated, full length {len(line)} chars]")
    return line


def format_observation(call: ToolCall, result: ToolResult,
                       truncate_chars: int = 2000,
                       obs_token: str | None = None) -> str:
    """Deterministic observation fed back to the model as a user message.

    v0.1.1 (FIXLIST item 15): with ``obs_token`` (a per-session random
    token the loop generates), tool OUTPUT is enclosed in delimiters that
    embedded content cannot guess or reproduce, so fetched/read text cannot
    forge harness framing (fake TOOL/OK lines or fenced call markers).

    v0.1.2 (FIXLIST2 item 5): ERROR observations get exactly the same
    treatment. Tool error text is model-visible output too, and it can
    carry arbitrary model-controlled bytes (a gate denial reason echoes the
    requested path verbatim), so it is truncated and framed identically —
    the code line stays outside the frame so the failure stays legible."""
    head = f"TOOL {summary(call)}"
    if result.ok:
        body = truncate(result.output, truncate_chars)
        if obs_token:
            return (f"{head}\nOK\n<OBSERVATION {obs_token}>\n{body}\n"
                    f"</OBSERVATION {obs_token}>")
        return f"{head}\nOK\n{body}"
    code = result.error_code or "unknown"
    body = truncate(result.error or "", truncate_chars)
    if obs_token:
        return (f"{head}\nERROR {code}\n<OBSERVATION {obs_token}>\n{body}\n"
                f"</OBSERVATION {obs_token}>")
    return f"{head}\nERROR {code}: {body}"


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
