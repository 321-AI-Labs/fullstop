"""fullstop CLI: run / resume / log / status / ui. JSON everywhere, no YAML.

``main(argv, approver)`` is a seam: tests pass ScriptedApprover so no test can
ever block on a tty prompt; the CLI defaults to InteractiveApprover. With
``--ui`` the approver is the browser UiApprover (decision-file protocol);
an injected approver still wins over ``--ui``.

v0.1.1 (FIXLIST items 2, 4, 16):
- ``run --goal`` overrides the manifest goal BEFORE the loop (and thus the
  system prompt) is built, so the model actually works toward the override;
- the manifest's ``log_truncate_chars`` reaches the ActivityLog;
- ``resume`` refuses a changed config (manifest/policy hash mismatch vs the
  last ``config_loaded`` event) unless ``--allow-config-change`` is passed.
"""

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from .agent import AgentLoop, InteractiveApprover
from .activity import ActivityLog
from .manifest import Manifest, load_manifest
from .policy import load_policy, policy_from_dict
from .provider import build_provider
from .redact import Redactor
from .state import RunState, activity_path, checkpoint_path, load_checkpoint
from .ui_approver import DEFAULT_TIMEOUT_S, UiApprover
from .ui_server import DEFAULT_PORT, UiServer
from . import ui_strings

_OK_STATUSES = ("completed", "stopped_max_steps", "stopped_max_cost",
                "script_exhausted")


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        print(json.dumps({"error": f"usage: {message}"}), file=sys.stderr)
        raise SystemExit(2)


def _add_ui_args(p, with_run_flags: bool = False) -> None:
    if with_run_flags:
        p.add_argument("--ui", action="store_true", help=ui_strings.UI_RUN_FLAG_HELP)
    p.add_argument("--no-browser", action="store_true",
                   help=ui_strings.UI_NO_BROWSER_HELP)
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=ui_strings.UI_PORT_HELP)
    if with_run_flags:
        p.add_argument("--approval-timeout", type=float,
                       default=DEFAULT_TIMEOUT_S,
                       help=ui_strings.UI_APPROVAL_TIMEOUT_HELP)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="fullstop", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="start a new run")
    p_run.add_argument("--manifest", required=True)
    p_run.add_argument("--goal", default=None,
                       help="override the manifest goal")
    _add_ui_args(p_run, with_run_flags=True)

    p_resume = sub.add_parser("resume", help="continue from the checkpoint")
    p_resume.add_argument("--manifest", required=True)
    p_resume.add_argument("--allow-config-change", action="store_true",
                          help="resume even though the manifest/policy bytes "
                               "changed since the last run")
    _add_ui_args(p_resume, with_run_flags=True)

    p_log = sub.add_parser("log", help="read the activity log")
    p_log.add_argument("--manifest", required=True)
    p_log.add_argument("--tail", type=int, default=20)
    p_log.add_argument("--verify", action="store_true")

    p_status = sub.add_parser("status", help="print the checkpointed run state")
    p_status.add_argument("--manifest", required=True)

    p_ui = sub.add_parser("ui", help=ui_strings.UI_COMMAND_HELP)
    p_ui.add_argument("--manifest", required=True,
                      help=ui_strings.UI_MANIFEST_HELP)
    _add_ui_args(p_ui)
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


def _config_hashes(manifest_path: str, manifest: Manifest) -> dict[str, str]:
    """sha256 of the effective config bytes: the manifest file, and the
    policy file (canonical JSON for the inline form). Anchors the
    ``config_loaded`` events and the resume mismatch check."""
    m_hash = hashlib.sha256(
        Path(manifest_path).read_bytes()).hexdigest()
    if manifest.policy_path is not None:
        p_hash = hashlib.sha256(
            manifest.policy_path.read_bytes()).hexdigest()
    else:
        p_hash = hashlib.sha256(json.dumps(
            manifest.inline_policy or {}, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
    return {"manifest_sha256": m_hash, "policy_sha256": p_hash}


def _refuse_changed_config(log: ActivityLog, hashes: dict[str, str],
                           allow: bool) -> None:
    last = None
    for entry in log.entries():
        if entry.get("event") == "config_loaded":
            last = entry
    if last is None or allow:
        return
    if (last.get("manifest_sha256") != hashes["manifest_sha256"]
            or last.get("policy_sha256") != hashes["policy_sha256"]):
        raise RuntimeError(
            "config changed since the last run (manifest/policy hash "
            "mismatch vs the last config_loaded event); refusing to resume "
            "under swapped gate rules — pass --allow-config-change to "
            "override")


def main(argv: list[str] | None = None,
         approver=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    if not args.command:
        print(json.dumps({"error": "usage: a command is required "
                                   "(run, resume, log, status, ui)"}), file=sys.stderr)
        return 2
    try:
        return _dispatch(args, approver)
    except Exception as e:  # runtime error -> JSON on stderr, exit 1
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 1


def _pick_approver(args, injected, home: Path, redactor: Redactor):
    """Approver precedence (seam law): an injected approver (tests) always
    wins; --ui selects the browser UiApprover; the default stays the
    unchanged InteractiveApprover."""
    if injected is not None:
        return injected
    if getattr(args, "ui", False):
        return UiApprover(home, redactor=redactor,
                          timeout_s=args.approval_timeout)
    return InteractiveApprover()


def _dispatch(args, approver) -> int:
    manifest = load_manifest(args.manifest)
    policy = _load_policy_for(manifest)
    home = Path(manifest.identity.home)
    hashes = _config_hashes(args.manifest, manifest)

    if args.command == "ui":
        server = UiServer(home, port=args.port,
                          open_browser=not args.no_browser)
        print(ui_strings.URL_LINE.format(url=server.start()), flush=True)
        server.serve_forever()
        return 0

    if args.command == "run":
        # FIXLIST item 4: apply the goal override BEFORE the loop is built so
        # the system prompt (built from manifest.goal) carries the override.
        if args.goal:
            manifest = replace(manifest, goal=args.goal)
        redactor = _redactor_for(manifest)
        provider = build_provider(manifest.provider)
        log = ActivityLog(activity_path(home), redactor=redactor,
                          truncate_chars=manifest.log_truncate_chars)
        server = None
        if args.ui:
            server = UiServer(home, port=args.port,
                              open_browser=not args.no_browser)
            print(ui_strings.RUN_UI_URL_LINE.format(url=server.start()),
                  flush=True)
        try:
            loop = AgentLoop(manifest, policy, provider, log, redactor,
                             approver=_pick_approver(args, approver, home,
                                                     redactor),
                             config_hashes=hashes)
            state = loop.new_state()
            state.manifest_path = str(Path(args.manifest).resolve())
            state = loop.run(state)
        finally:
            if server is not None:
                server.stop()
        print(json.dumps({"run_id": state.run_id, "status": state.status,
                          "steps_done": state.steps_done,
                          "cost_usd": state.cost_usd}))
        return 0 if state.status in _OK_STATUSES else 1

    if args.command == "resume":
        redactor = _redactor_for(manifest)
        state = load_checkpoint(checkpoint_path(home))
        provider = build_provider(manifest.provider,
                                  start_cursor=state.script_cursor)
        log = ActivityLog(activity_path(home), redactor=redactor,
                          truncate_chars=manifest.log_truncate_chars)
        _refuse_changed_config(
            log, hashes, allow=bool(args.allow_config_change))
        server = None
        if args.ui:
            server = UiServer(home, port=args.port,
                              open_browser=not args.no_browser)
            print(ui_strings.RUN_UI_URL_LINE.format(url=server.start()),
                  flush=True)
        try:
            loop = AgentLoop(manifest, policy, provider, log, redactor,
                             approver=_pick_approver(args, approver, home,
                                                     redactor),
                             config_hashes=hashes)
            state = loop.run(state)
        finally:
            if server is not None:
                server.stop()
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

