"""RunState and atomic, scrubbed checkpoints."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .redact import Redactor


@dataclass
class RunState:
    run_id: str
    manifest_path: str
    goal: str
    status: str
    steps_done: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    script_cursor: int = 0
    messages: list = field(default_factory=list)
    failure: str | None = None

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "manifest_path": self.manifest_path,
            "goal": self.goal,
            "status": self.status,
            "steps_done": self.steps_done,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "script_cursor": self.script_cursor,
            "messages": self.messages,
            "failure": self.failure,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        return cls(
            run_id=data["run_id"],
            manifest_path=data.get("manifest_path", ""),
            goal=data.get("goal", ""),
            status=data["status"],
            steps_done=int(data.get("steps_done", 0)),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            script_cursor=int(data.get("script_cursor", 0)),
            messages=list(data.get("messages", [])),
            failure=data.get("failure"),
        )


def checkpoint_path(home: Path) -> Path:
    return Path(home) / ".hearth" / "state.json"


def activity_path(home: Path) -> Path:
    return Path(home) / ".hearth" / "activity.jsonl"


def _scrub_messages(messages: list, redactor: Redactor | None) -> list:
    if redactor is None:
        return messages
    scrubbed = []
    for msg in messages:
        if isinstance(msg, dict):
            msg = {k: (redactor.scrub(v) if isinstance(v, str) else v)
                   for k, v in msg.items()}
        scrubbed.append(msg)
    return scrubbed


def save_checkpoint(state: RunState, path: Path,
                    redactor: Redactor | None = None) -> None:
    """Atomic write via '<path>.tmp' + os.replace; messages scrubbed first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = state.to_dict()
    data["messages"] = _scrub_messages(data["messages"], redactor)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_checkpoint(path: Path) -> RunState:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunState.from_dict(data)
