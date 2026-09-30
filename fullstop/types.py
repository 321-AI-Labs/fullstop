"""Shared value objects and vocabularies. Imported by every other module."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Action(Enum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]      # flat values: str|int|float|bool|None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str               # always a str; "" on failure
    error: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class GateDecision:
    action: Action
    reason: str
    token: str | None = None  # set iff ALLOW; minted by Gate._mint


NOTE_FILENAME: str = "memory.md"
# Single source of truth for the note tool's target. NOT configurable in v0.1
# so the gate's policy matching and the tool's actual target cannot drift.

EVENTS: tuple[str, ...] = (
    "run_start", "resume", "model_reply", "tool_call",
    "gate_decision", "approval_request", "approval_response", "sandbox_block",
    "tool_result", "step", "checkpoint", "guard_trip", "config_loaded",
    "run_end",
)

ERROR_CODES: tuple[str, ...] = (
    "sandbox_escape", "protected_target", "unknown_tool",
    "not_implemented", "command_not_allowed", "shell_disabled",
    "domain_denied", "malformed_url", "unsupported_media_type", "timeout",
    "io_error", "malformed_call", "denied_by_policy", "denied_by_operator",
)
# INVARIANT: every code has at least one producer (asserted by the
# producer-enumeration tests). provider_error was dropped because a
# ProviderError becomes run status "failed", never a ToolResult; too_large was
# dropped because oversize bodies/files truncate with ok=True, never an error.

RUN_STATUSES: tuple[str, ...] = (
    "running", "completed", "failed",
    "stopped_max_steps", "stopped_max_cost", "script_exhausted",
    # v0.1.1 (FIXLIST items 3 and 10): context exhaustion and unattended
    # approval each get a distinct, resumable stop status.
    "stopped_context", "stopped_approval",
)
