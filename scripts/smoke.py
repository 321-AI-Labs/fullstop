"""DoD#2 smoke: keyless AND socketless, honestly.

Pinned theater (R2-B2): 7 scripted replies; exactly 3 approval prompts in
pinned order (consequential write -> approve, web A -> approve, web B -> deny);
the escape write rides the pre-approved glob 'notes/../../escape.txt'
(fnmatch True vs 'notes/**', verified) so it consumes NO prompt and is stopped
by the sandbox; the approved fetch is served by an injected fake through the
AgentLoop fetch seam (socketless AND real, not theater). Exit 0 iff all 8
assertions pass. FULLSTOP_SMOKE_KEEP=1 keeps the workspace.
"""

import json
import os
import shutil
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fullstop.activity import ActivityLog                      # noqa: E402
from fullstop.agent import AgentLoop, ScriptedApprover          # noqa: E402
from fullstop.manifest import Identity, Limits, Manifest, ProviderConfig  # noqa: E402
from fullstop.policy import policy_from_dict                    # noqa: E402
from fullstop.provider import ScriptedModel                     # noqa: E402
from fullstop.redact import Redactor                            # noqa: E402
from fullstop.state import activity_path                        # noqa: E402


def call_block(name: str, args: dict) -> str:
    return ("<<<TOOL_CALL>>>\n"
            + json.dumps({"name": name, "args": args})
            + "\n<<<END_TOOL_CALL>>>")


def main() -> int:
    out_root = REPO / ".smoke-out"
    out_root.mkdir(exist_ok=True)
    work = out_root / f"run-{uuid.uuid4().hex[:8]}"
    home = work / "home"
    (home / ".fullstop").mkdir(parents=True)
    (home / "fixture.txt").write_text("smoke fixture content", encoding="utf-8")

    def files_outside_home():
        return sorted(str(p) for p in work.rglob("*")
                      if p.is_file() and home not in p.parents)

    before_outside = files_outside_home()

    # Inline policy PINNED.
    policy_data = {
        "write_preapproved": ["notes/**", "memory.md"],
        "protected_paths": ["secret-*.txt"],
        "shell": {"allow": []},          # disabled
        "web": {"allow_domains": []},    # every fetch approval-gated
    }
    policy = policy_from_dict(policy_data)

    replies = [
        # r1: note (pre-approved via memory.md, NO prompt) + protected-free read
        "Starting.\n" + call_block("note", {"text": "smoke run started"}) + "\n"
        + call_block("file_read", {"path": "fixture.txt"}),
        # r2: pre-approved glob write (NO prompt)
        call_block("file_write",
                   {"path": "notes/allowed.md", "content": "allowed by glob"}),
        # r3: planted consequential action -> PROMPT 1 (approve)
        call_block("file_write",
                   {"path": "report.md", "content": "planted consequential write"}),
        # r4: web on empty allowlist -> PROMPT 2 (approve; fake fetch serves it)
        call_block("web_fetch", {"url": "https://approval-test.example/a"}),
        # r5: web again -> PROMPT 3 (deny)
        call_block("web_fetch", {"url": "https://approval-test.example/b"}),
        # r6: glob-disguised escape (verified: fnmatch('notes/../../escape.txt',
        #     'notes/**') == True -> gate ALLOW, NO prompt) -> sandbox blocks it
        call_block("file_write",
                   {"path": "notes/../../escape.txt", "content": "nope"}),
        # r7: final summary, no tool call -> completed
        "Smoke finished: gate, approval, denial, and sandbox all exercised.",
    ]

    manifest = Manifest(
        identity=Identity(name="smoke", role="smoke agent", home=home),
        goal="smoke goal",
        policy_path=None,
        inline_policy=policy_data,
        provider=ProviderConfig(type="scripted",
                                script_path=home / ".fullstop" / "script.json"),
        limits=Limits(max_steps=12),
    )

    fetch_calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float):
        fetch_calls.append(url)
        return 200, "text/plain", b"smoke-body"

    log = ActivityLog(activity_path(home))
    approver = ScriptedApprover([True, True, False])
    loop = AgentLoop(manifest, policy, ScriptedModel(replies), log,
                     Redactor({}), approver=approver, fetch=fake_fetch)
    state = loop.run(loop.new_state())

    entries = [json.loads(line) for line in
               activity_path(home).read_text(encoding="utf-8").splitlines()
               if line.strip()]
    results = [e for e in entries if e["event"] == "tool_result"]
    # result order: note, read fixture, allowed write, report write, web A,
    # web B, escape
    r_note, r_read, r_allowed, r_report, r_web_a, r_web_b, r_escape = results

    checks: list[tuple[str, bool]] = []
    checks.append(("(1) final status completed", state.status == "completed"))
    checks.append(("(2) report.md exists (approved consequential write executed)",
                   (home / "report.md").exists()))
    checks.append(("(3) web A ok with body; fake fetch called exactly once",
                   r_web_a["ok"] is True and "smoke-body" in r_web_a["output"]
                   and len(fetch_calls) == 1))
    checks.append(("(4) web B denied_by_operator; fetch still called exactly once",
                   r_web_b["error_code"] == "denied_by_operator"
                   and len(fetch_calls) == 1))
    checks.append(("(5) approver consumed exactly 3 prompts "
                   "(escape was glob-pre-approved, not operator-approved)",
                   len(approver.prompts) == 3))
    sandbox_blocks = [e for e in entries if e["event"] == "sandbox_block"]
    checks.append(("(6) escape blocked: no file outside workspace, "
                   "sandbox_block present, error_code sandbox_escape",
                   files_outside_home() == before_outside
                   and len(sandbox_blocks) == 1
                   and r_escape["error_code"] == "sandbox_escape"
                   and not (work / "escape.txt").exists()))
    step_events = [e for e in entries if e["event"] == "step"]
    checks.append(("(7) run_start..run_end present; step events == steps_done",
                   entries[0]["event"] == "run_start"
                   and entries[-1]["event"] == "run_end"
                   and len(step_events) == state.steps_done))
    checks.append(("(8) ActivityLog.verify() == (True, None)",
                   ActivityLog(activity_path(home)).verify() == (True, None)))

    failed = 0
    for name, ok in checks:
        print(("PASS: " if ok else "FAIL: ") + name)
        if not ok:
            failed += 1
    print(f"status={state.status} steps_done={state.steps_done} "
          f"prompts={len(approver.prompts)} fetch_calls={len(fetch_calls)}")

    if os.environ.get("FULLSTOP_SMOKE_KEEP") != "1":
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
