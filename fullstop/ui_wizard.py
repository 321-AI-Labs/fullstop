"""The new-run wizard: a form-driven manifest builder, nothing more.

Charter scope 4: the wizard "writes a manifest file only; it never runs
anything by itself". This implementation writes exactly ONE file — a
manifest whose policy is INLINE (manifest.py supports the inline form), so
no second policy file is ever produced and the UI's write surface stays a
single new-file-only artifact.

Everything the form produces goes through the EXISTING strict validators
(``manifest_from_dict`` + ``policy_from_dict``) BEFORE any file is written,
and validation errors are surfaced field-level, VERBATIM — the mapping never
rewrites a validator sentence; lines that cannot be mapped to a field are
shown verbatim in the general list, never dropped.

Credential law: the form carries env var NAMES only. As a belt-and-braces
guard the bytes written are scrubbed through ``Redactor.from_env`` over every
name the form mentions, so an environment VALUE can never be persisted by
the wizard even if one were pasted into a text field.
"""

import json
import os
import re
from pathlib import Path

from .manifest import ManifestError, manifest_from_dict
from .policy import PolicyError, policy_from_dict
from .redact import Redactor
from . import ui_strings

_ERROR_LINE_RE = re.compile(r"^- (.+)$")

# validator key -> wizard form field. Keys match the START of the line body
# after the "- " bullet (longest first below); whole-line keys cover the
# provider messages that carry no dotted prefix.
_KEY_TO_FIELD = {
    "identity.name": "name",
    "identity.role": "role",
    "identity.home": "home",
    "identity": "name",
    "goal": "goal",
    "credentials": "credentials",
    "provider.type": "providerType",
    "provider.base_url": "baseUrl",
    "provider.api_key_env": "apiKeyEnv",
    "provider.model": "model",
    "provider.usd_per_1k_input": "priceIn",
    "provider.usd_per_1k_output": "priceOut",
    "provider.script_path": "scriptPath",
    "provider.timeout_s": "providerType",
    "provider": "providerType",
    "openai_compat provider requires base_url": "baseUrl",
    "openai_compat provider requires api_key_env": "apiKeyEnv",
    "openai_compat provider requires model": "model",
    "scripted provider requires script_path": "scriptPath",
    "limits.max_steps": "maxSteps",
    "limits.max_cost_usd": "maxCost",
    "limits.max_tool_calls_per_step": "maxCalls",
    "limits.max_context_chars": "maxContext",
    "limits": "maxSteps",
    "log_truncate_chars": "logTruncate",
    "protected_paths": "protected",
    "write_preapproved": "preapproved",
    "shell.allow": "shellAllow",
    "shell.deny": "shellDeny",
    "shell.timeout_s": "shellDeny",
    "shell": "shellAllow",
    "web.allow_domains": "webAllow",
    "web.deny_domains": "webDeny",
    "web.timeout_s": "webDeny",
    "web.max_bytes": "webDeny",
    "web": "webAllow",
}
_KEYS_BY_LENGTH = sorted(_KEY_TO_FIELD, key=len, reverse=True)


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _num_or_raw(text: str):
    """Numeric fields pass the RAW text through when it is not a clean
    number: the strict validator then rejects it with its own verbatim
    message instead of the wizard inventing one."""
    t = (text or "").strip()
    if t == "":
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return t


def _shell_allow_entries(text: str) -> list:
    """Each line: a bare program string, or a JSON object with exactly
    ``program`` and ``args``. Non-JSON lines stay strings; the policy
    validator is the authority on what is legal."""
    out: list = []
    for ln in _lines(text):
        if ln.startswith("{"):
            try:
                out.append(json.loads(ln))
                continue
            except json.JSONDecodeError:
                pass  # fall through: the validator rejects it verbatim
        out.append(ln)
    return out


def build_manifest_dict(form: dict) -> dict:
    """Form -> manifest dict with an INLINE policy. Raw garbage survives
    into the dict on purpose: the validators get to say it, not us."""
    get = lambda k: (form.get(k) or "").strip()  # noqa: E731

    provider_type = get("providerType") or "scripted"
    provider: dict = {"type": provider_type}
    if provider_type == "scripted":
        if get("scriptPath"):
            provider["script_path"] = get("scriptPath")
    else:
        for key, field in (("base_url", "baseUrl"), ("api_key_env", "apiKeyEnv"),
                           ("model", "model")):
            if get(field):
                provider[key] = get(field)
        for key, field in (("usd_per_1k_input", "priceIn"),
                           ("usd_per_1k_output", "priceOut")):
            value = _num_or_raw(get(field))
            if value is not None:
                provider[key] = value

    limits: dict = {}
    for key, field in (("max_steps", "maxSteps"),
                       ("max_tool_calls_per_step", "maxCalls"),
                       ("max_context_chars", "maxContext")):
        value = _num_or_raw(get(field))
        if value is not None:
            limits[key] = value
    value = _num_or_raw(get("maxCost"))
    if value is not None:
        limits["max_cost_usd"] = value

    policy: dict = {}
    if _lines(form.get("protected")):
        policy["protected_paths"] = _lines(form.get("protected"))
    if _lines(form.get("preapproved")):
        policy["write_preapproved"] = _lines(form.get("preapproved"))
    shell: dict = {}
    if _lines(form.get("shellAllow")):
        shell["allow"] = _shell_allow_entries(form.get("shellAllow"))
    if _lines(form.get("shellDeny")):
        shell["deny"] = _lines(form.get("shellDeny"))
    if shell:
        policy["shell"] = shell
    web: dict = {}
    if _lines(form.get("webAllow")):
        web["allow_domains"] = _lines(form.get("webAllow"))
    if _lines(form.get("webDeny")):
        web["deny_domains"] = _lines(form.get("webDeny"))
    if web:
        policy["web"] = web

    manifest: dict = {
        "identity": {"name": get("name"), "role": get("role"),
                     "home": get("home")},
        "goal": get("goal"),
        "policy": policy,
        "provider": provider,
        "limits": limits,
    }
    creds = _names(get("credentials"))
    if creds:
        manifest["credentials"] = creds
    value = _num_or_raw(get("logTruncate"))
    if value is not None:
        manifest["log_truncate_chars"] = value
    return manifest


def _names(text: str) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


_HEADERS = ("invalid manifest:", "invalid policy:")


def _map_errors(message: str) -> tuple[dict[str, list[str]], list[str]]:
    """'- key: message' lines -> (field -> [messages], unmapped lines).

    Longest-key-first matching so limits.max_steps wins over limits. Every
    line survives verbatim in exactly one of the two buckets; the only lines
    dropped are the exceptions' own headers ("invalid manifest:"), which
    carry no error content."""
    field_errors: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for line in message.splitlines():
        stripped = line.strip()
        if not stripped or stripped in _HEADERS:
            continue
        m = _ERROR_LINE_RE.match(stripped)
        if not m:
            unmapped.append(stripped)
            continue
        body = m.group(1)
        for key in _KEYS_BY_LENGTH:
            if body == key or body.startswith(key + " "):
                field = _KEY_TO_FIELD[key]
                field_errors.setdefault(field, []).append(body)
                break
        else:
            unmapped.append(body)
    return field_errors, unmapped


def validate_form(form: dict, base_dir: str | Path | None = None) -> dict:
    """Run the REAL validators; return the wire payload for the dashboard."""
    manifest = build_manifest_dict(form)
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    field_errors: dict[str, list[str]] = {}
    global_errors: list[str] = []
    try:
        manifest_from_dict(manifest, base_dir=base)
    except ManifestError as e:
        f, g = _map_errors(str(e))
        for k, v in f.items():
            field_errors.setdefault(k, []).extend(v)
        global_errors.extend(g)
    try:
        policy_from_dict(manifest["policy"])
    except PolicyError as e:
        f, g = _map_errors(str(e))
        for k, v in f.items():
            field_errors.setdefault(k, []).extend(v)
        global_errors.extend(g)
    ok = not field_errors and not global_errors
    return {"ok": ok, "field_errors": field_errors,
            "global_errors": global_errors, "manifest": manifest}


def save_manifest(form: dict) -> dict:
    """Validate, then write ONE new manifest file (inline policy). Refuses
    existing paths and paths inside the workspace home (config-tamper law).
    Returns the wire payload; on any refusal ok=False with the reason."""
    path_text = (form.get("savePath") or "").strip()
    if not path_text:
        return {"ok": False, "field_errors": {"savePath": ["savePath"]},
                "global_errors": []}
    path = Path(path_text)
    base = path.parent if str(path.parent) else Path.cwd()
    verdict = validate_form(form, base_dir=base)
    if not verdict["ok"]:
        return verdict
    if path.exists():
        return {"ok": False,
                "field_errors": {"savePath": [ui_strings.WIZARD_PATH_EXISTS]},
                "global_errors": []}
    home_text = (form.get("home") or "").strip()
    if home_text:
        home = Path(home_text)
        if not home.is_absolute():
            home = base / home
        try:
            if path.resolve().is_relative_to(home.resolve()):
                return {"ok": False,
                        "field_errors": {
                            "savePath": [ui_strings.WIZARD_PATH_INSIDE_HOME]},
                        "global_errors": []}
        except (OSError, ValueError):
            pass
    manifest = verdict["manifest"]
    names = list(manifest.get("credentials", []))
    api_env = manifest.get("provider", {}).get("api_key_env")
    if api_env:
        names.append(api_env)
    text = json.dumps(manifest, indent=2)
    text = Redactor.from_env(names).scrub(text)  # values can never persist
    # No-clobber must be ATOMIC, not a check-then-write: a file created in
    # the gap between the path.exists() check above and the write must never
    # be overwritten. Write a tmp sibling (same directory, so same volume)
    # and hard-link it into place: os.link fails with FileExistsError if the
    # destination exists, on POSIX and on NTFS alike. Filesystems without
    # hard links fail closed with an OSError; nothing is ever clobbered.
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text + "\n", encoding="utf-8")
        try:
            os.link(tmp, path)
        except FileExistsError:
            return {"ok": False,
                    "field_errors": {
                        "savePath": [ui_strings.WIZARD_PATH_EXISTS]},
                    "global_errors": []}
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as e:
        return {"ok": False, "field_errors": {},
                "global_errors": [f"{type(e).__name__}: {e}"]}
    return {"ok": True, "saved_path": str(path),
            "bytes": len(text.encode("utf-8")) + 1}
