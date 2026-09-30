"""Shared helpers for the fullstop test suite.

No tests/__init__.py by design — `python -m unittest discover -s tests` from
the repo root puts tests/ on sys.path. The repo root itself is bootstrapped
below so `import fullstop` works regardless of the runner's CWD or
PYTHONSAFEPATH/-P behavior (the suite once failed with ModuleNotFoundError
when a runner launched discovery without CWD on sys.path).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Bootstrap BEFORE any fullstop import: make the repo root importable no matter
# how discovery was launched. Every test module imports support first.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fullstop.activity import ActivityLog
from fullstop.agent import AgentLoop
from fullstop.manifest import Identity, Limits, Manifest, ProviderConfig
from fullstop.policy import Policy
from fullstop.provider import ScriptedModel
from fullstop.redact import Redactor
from fullstop.state import activity_path

# Symlink tests skip (never silently) when the platform refuses; the count is
# carried in the skip message.
SYMLINK_SKIPS: list[str] = []


def temp_dir():
    return tempfile.TemporaryDirectory()


def make_home(base: Path, name: str = "home") -> Path:
    home = base / name
    (home / ".fullstop").mkdir(parents=True, exist_ok=True)
    return home


def call_block(name: str, args: dict) -> str:
    return ("<<<TOOL_CALL>>>\n"
            + json.dumps({"name": name, "args": args})
            + "\n<<<END_TOOL_CALL>>>")


def write_script(base: Path, replies) -> Path:
    path = Path(base) / "script.json"
    path.write_text(json.dumps(list(replies)), encoding="utf-8")
    return path


def script_manifest(home: Path, script_path, max_steps: int = 12,
                    credentials=(), api_key_env=None, goal="test goal",
                    pricing=False) -> Manifest:
    provider = ProviderConfig(type="scripted", script_path=Path(script_path))
    if pricing:
        provider = ProviderConfig(type="scripted", script_path=Path(script_path),
                                  usd_per_1k_input=1.0, usd_per_1k_output=1.0)
    return Manifest(
        identity=Identity(name="tester", role="test agent", home=Path(home)),
        goal=goal,
        policy_path=None,
        inline_policy=None,
        credential_env_vars=tuple(credentials),
        provider=provider,
        limits=Limits(max_steps=max_steps),
    )


def build_loop(home: Path, replies, policy: Policy | None = None,
               approver=None, fetch=None, manifest: Manifest | None = None,
               provider=None, redactor: Redactor | None = None,
               log: ActivityLog | None = None):
    """Build an AgentLoop over a temp workspace with a scripted model."""
    if manifest is None:
        manifest = script_manifest(home, write_script(home / ".fullstop", replies))
    if provider is None:
        provider = ScriptedModel(list(replies))
    redactor = redactor or Redactor({})
    if log is None:
        # The log MUST carry the redactor — scrubbing happens at write time.
        log = ActivityLog(activity_path(home), redactor=redactor)
    return AgentLoop(manifest, policy or Policy(), provider, log,
                     redactor, approver=approver, fetch=fetch)


def read_events(home: Path) -> list[dict]:
    text = activity_path(home).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def events_of(entries: list[dict], name: str) -> list[dict]:
    return [e for e in entries if e.get("event") == name]


def tool_results(entries: list[dict]) -> list[dict]:
    return events_of(entries, "tool_result")


def snapshot_files(root: Path) -> set:
    return {str(p.resolve()) for p in root.rglob("*") if p.is_file()}


def fake_fetch(status: int = 200, content_type: str = "text/plain",
               body: bytes = b"body", calls: list | None = None, exc=None):
    def _fetch(url: str, timeout_s: float):
        if calls is not None:
            calls.append(url)
        if exc is not None:
            raise exc
        return status, content_type, body
    return _fetch


def gate_harness(tmp: Path, policy=None):
    """Gate + registry over a fresh temp home, for fix-list tests that drive
    the decide->approve->execute boundary directly (as test_tools does)."""
    from fullstop.gate import Gate
    from fullstop.policy import Policy as _Policy
    from fullstop.tools import build_registry as _build

    class _Harness:
        def __init__(self, testcase_tmp, pol):
            self.home = make_home(Path(testcase_tmp))
            self.policy = pol or _Policy()
            self.gate = Gate(self.policy, self.home)
            self.registry = _build(self.home, self.policy, Redactor({}),
                                   self.gate.verify)

        def approved(self, call):
            from fullstop.types import Action
            decision = self.gate.decide(call)
            if decision.action is Action.APPROVAL_REQUIRED:
                decision = self.gate.resolve_approval(call, True)
            return decision

        def execute(self, call):
            return self.registry.execute(call, self.approved(call))

    return _Harness(tmp, policy)


def try_symlink(testcase, target, link: Path, label: str) -> None:
    """Create a symlink or skip the test WITH a count — never silently pass."""
    try:
        os.symlink(str(target), str(link),
                   target_is_directory=Path(target).is_dir())
    except (OSError, NotImplementedError) as e:
        SYMLINK_SKIPS.append(label)
        testcase.skipTest(
            f"symlink unavailable on this platform ({label}; "
            f"{len(SYMLINK_SKIPS)} symlink case(s) skipped so far): {e}")
