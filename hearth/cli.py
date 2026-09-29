"""hearth CLI: run / resume / log / status. JSON everywhere, no YAML (CHARTER.md:13).

``main(argv, approver)`` is a seam: tests pass ScriptedApprover so no test can
ever block on a tty prompt; the CLI defaults to InteractiveApprover.
"""

import argparse
import json
import sys
from pathlib import Path

from .agent import AgentLoop, InteractiveApprover
from .activity import ActivityLog
from .manifest import load_manifest
from .policy import load_policy, policy_from_dict
from .provider import build_provider
from .redact import Redactor
from .state import activity_path, checkpoint_path, load_checkpoint

_OK_STATUSES = ("completed", "stopped_max_steps", "stopped_max_cost",
                "script_exhausted")


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        print(json.dumps({"error": f"usage: {message}"}), file=sys.stderr)
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="hearth", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="start a new run")
    p_run.add_argument("--manifest", required=True)
    p_run.add_argument("--goal", default=None,
                       help="override the manifest goal")

    p_resume = sub.add_parser("resume", help="continue from the checkpoint")
    p_resume.add_argument("--manifest", required=True)

    p_log = sub.add_parser("log", help="read the activity log")
    p_log.add_argument("--manifest", required=True)
    p_log.add_argument("--tail", type=int, default=20)
    p_log.add_argument("--verify", action="store_true")

    p_status = sub.add_parser("status", help="print the checkpointed run state")
    p_status.add_argument("--manifest", required=True)
    return parser


def _load_policy_for(manifest):
    if manifest.policy_path is not None:
        return load_policy(manifest.policy_path)
    return policy_from_dict(manifest.inline_policy or {})


def _redactor_for(manifest) -> Redactor:
    names = list(manifest.credential_env_vars)
    if manifest.provider.api_key_env:
        names.append(manifest.provider.api_key_env)
    return Redactor.from_env(names)


def main(argv: list[str] | None = None,
         approver=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    if not args.command:
        print(json.dumps({"error": "usage: a command is required "
                                   "(run, resume, log, status)"}), file=sys.stderr)
        return 2
    try:
        return _dispatch(args, approver)
    except Exception as e:  # runtime error -> JSON on stderr, exit 1
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1


def _dispatch(args, approver) -> int:
    manifest = load_manifest(args.manifest)
    policy = _load_policy_for(manifest)
    home = Path(manifest.identity.home)

    if args.command == "run":
        redactor = _redactor_for(manifest)
        provider = build_provider(manifest.provider)
        log = ActivityLog(activity_path(home), redactor=redactor)
        loop = AgentLoop(manifest, policy, provider, log, redactor,
                         approver=approver if approver is not None
                         else InteractiveApprover())
        state = loop.new_state()
        state.manifest_path = str(Path(args.manifest).resolve())
        if args.goal:
            state.goal = args.goal
        state = loop.run(state)
        print(json.dumps({"run_id": state.run_id, "status": state.status,
                          "steps_done": state.steps_done,
                          "cost_usd": state.cost_usd}))
        return 0 if state.status in _OK_STATUSES else 1

    if args.command == "resume":
        redactor = _redactor_for(manifest)
        state = load_checkpoint(checkpoint_path(home))
        provider = build_provider(manifest.provider,
                                  start_cursor=state.script_cursor)
        log = ActivityLog(activity_path(home), redactor=redactor)
        loop = AgentLoop(manifest, policy, provider, log, redactor,
                         approver=approver if approver is not None
                         else InteractiveApprover())
        state = loop.run(state)
        print(json.dumps({"run_id": state.run_id, "status": state.status,
                          "steps_done": state.steps_done,
                          "cost_usd": state.cost_usd}))
        return 0 if state.status in _OK_STATUSES else 1

    if args.command == "log":
        log = ActivityLog(activity_path(home))
        if args.verify:
            ok, first_bad = log.verify()
            print(json.dumps({"verified": ok, "first_bad_seq": first_bad}))
            return 0 if ok else 1
        for entry in log.tail(args.tail):
            print(json.dumps(entry))
        return 0

    if args.command == "status":
        path = checkpoint_path(home)
        if not path.exists():
            print(json.dumps({"error": f"no checkpoint at {path}"}),
                  file=sys.stderr)
            return 1
        state = load_checkpoint(path)
        print(json.dumps(state.to_dict()))
        return 0

    print(json.dumps({"error": f"unknown command: {args.command}"}),
          file=sys.stderr)
    return 2
