"""Strict manifest validation. One manifest = one specialist agent (CHARTER.md:18).

Normative path rule: ``identity.home``, the ``policy`` path form, and
``provider.script_path`` ALL resolve relative to the MANIFEST FILE's directory.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .activity import _TAIL_WINDOW_BYTES

# v0.1.2 (FIXLIST2 item 3): log_truncate_chars must stay safely below the
# activity log's bounded tail window — at or above it, a single
# truncated-at-cap string can produce a line the tail scan can never
# validate again (the log-wedge). The window is imported (not duplicated)
# so the two limits cannot drift apart silently.
LOG_TRUNCATE_MAX = _TAIL_WINDOW_BYTES


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Identity:
    name: str
    role: str
    home: Path  # RESOLVED absolute


@dataclass(frozen=True)
class ProviderConfig:
    type: str
    base_url: str | None = None
    api_key_env: str | None = None
    model: str | None = None
    usd_per_1k_input: float | None = None
    usd_per_1k_output: float | None = None
    timeout_s: float = 60.0
    script_path: Path | None = None  # RESOLVED absolute


@dataclass(frozen=True)
class Limits:
    max_steps: int
    max_cost_usd: float | None = None
    max_tool_calls_per_step: int = 4
    # v0.1.1 (FIXLIST item 3): near-limit context estimate in characters
    # (sum of message content lengths); reaching it stops the run in
    # ``stopped_context`` BEFORE the next model call.
    max_context_chars: int | None = None


@dataclass(frozen=True)
class Manifest:
    identity: Identity
    goal: str
    policy_path: Path | None
    inline_policy: dict | None
    # kw_only resolves the field order the plan pins (a defaulted field before
    # non-defaulted provider/limits); all construction is by keyword.
    credential_env_vars: tuple[str, ...] = field(default=(), kw_only=True)
    provider: ProviderConfig
    limits: Limits
    log_truncate_chars: int = 2000


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ManifestError(f"cannot read manifest {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest {path} is not valid JSON: {e}") from e
    manifest = manifest_from_dict(data, base_dir=path.parent)
    # Config-tamper guard, file side: the manifest itself must not sit inside
    # the sandbox it defines (inline-policy manifests included).
    manifest_dir = path.resolve().parent
    if _inside(manifest_dir, manifest.identity.home):
        raise ManifestError(
            f"manifest {path} sits inside identity.home "
            f"{manifest.identity.home}: the gate's config must stay outside "
            f"the agent's sandbox (config-tamper protection)")
    return manifest


def _resolve(base_dir: Path, value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = base_dir / p
    return p.resolve()


def _inside(child: Path, parent: Path) -> bool:
    """True iff ``child`` equals or lies below ``parent`` (both absolute)."""
    try:
        return child.is_relative_to(parent)
    except (TypeError, ValueError):
        return False


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def manifest_from_dict(data: dict, base_dir: str | Path | None = None) -> Manifest:
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    problems: list[str] = []

    if not isinstance(data, dict):
        raise ManifestError("- manifest must be a JSON object")

    # --- top-level keys -------------------------------------------------
    known_top = {"identity", "goal", "policy", "credentials", "provider",
                 "limits", "log_truncate_chars"}
    for key in sorted(set(data) - known_top):
        problems.append(f"- unknown manifest key: {key}")
    for req in ("identity", "goal", "policy", "provider", "limits"):
        if req not in data:
            problems.append(f"- missing required key: {req}")

    # --- identity --------------------------------------------------------
    identity_data = data.get("identity")
    name = role = ""
    home_raw: str | None = None
    if not isinstance(identity_data, dict):
        problems.append("- identity must be an object")
    else:
        for key in sorted(set(identity_data) - {"name", "role", "home"}):
            problems.append(f"- unknown identity key: {key}")
        name = identity_data.get("name")
        role = identity_data.get("role")
        home_raw = identity_data.get("home")
        if not isinstance(name, str) or not name:
            problems.append("- identity.name must be a non-empty string")
            name = ""
        if not isinstance(role, str) or not role:
            problems.append("- identity.role must be a non-empty string")
            role = ""
        if not isinstance(home_raw, str) or not home_raw:
            problems.append("- identity.home must be a non-empty string")
            home_raw = None

    # --- goal -------------------------------------------------------------
    goal = data.get("goal")
    if not isinstance(goal, str) or not goal:
        problems.append("- goal must be a non-empty string")
        goal = ""

    # --- policy -----------------------------------------------------------
    policy_val = data.get("policy")
    policy_path: Path | None = None
    inline_policy: dict | None = None
    if isinstance(policy_val, str) and policy_val:
        policy_path = _resolve(base, policy_val)
    elif isinstance(policy_val, dict):
        inline_policy = policy_val
    else:
        problems.append('- policy must be a path string or an inline object')

    # --- credentials --------------------------------------------------------
    creds = data.get("credentials", [])
    if not isinstance(creds, list) or not all(isinstance(c, str) and c for c in creds):
        problems.append("- credentials must be a list of env var names (strings)")
        creds = []

    # --- provider -----------------------------------------------------------
    prov = data.get("provider")
    ptype = base_url = api_key_env = model = None
    in_price = out_price = None
    timeout_s = 60.0
    script_path: Path | None = None
    if not isinstance(prov, dict):
        problems.append("- provider must be an object")
    else:
        for key in sorted(set(prov) - {"type", "base_url", "api_key_env", "model",
                                       "usd_per_1k_input", "usd_per_1k_output",
                                       "timeout_s", "script_path"}):
            problems.append(f"- unknown provider key: {key}")
        ptype = prov.get("type")
        if ptype not in ("openai_compat", "scripted"):
            problems.append('- provider.type must be "openai_compat" or "scripted"')
            ptype = ""
        if ptype == "openai_compat":
            base_url = prov.get("base_url")
            api_key_env = prov.get("api_key_env")
            model = prov.get("model")
            if not isinstance(base_url, str) or not base_url:
                problems.append("- openai_compat provider requires base_url")
            if not isinstance(api_key_env, str) or not api_key_env:
                problems.append("- openai_compat provider requires api_key_env")
            if not isinstance(model, str) or not model:
                problems.append("- openai_compat provider requires model")
            in_price = prov.get("usd_per_1k_input")
            out_price = prov.get("usd_per_1k_output")
            if in_price is not None and not _is_num(in_price):
                problems.append("- usd_per_1k_input must be a number")
                in_price = None
            if out_price is not None and not _is_num(out_price):
                problems.append("- usd_per_1k_output must be a number")
                out_price = None
            timeout_s = prov.get("timeout_s", 60.0)
            if not _is_num(timeout_s) or timeout_s <= 0:
                problems.append("- provider.timeout_s must be a positive number")
                timeout_s = 60.0
        elif ptype == "scripted":
            sp = prov.get("script_path")
            if not isinstance(sp, str) or not sp:
                problems.append("- scripted provider requires script_path")
            else:
                script_path = _resolve(base, sp)

    # --- limits -----------------------------------------------------------
    lim = data.get("limits")
    max_steps = 1
    max_cost = None
    max_calls = 4
    max_context = None
    if not isinstance(lim, dict):
        problems.append("- limits must be an object")
    else:
        for key in sorted(set(lim) - {"max_steps", "max_cost_usd",
                                      "max_tool_calls_per_step",
                                      "max_context_chars"}):
            problems.append(f"- unknown limits key: {key}")
        ms = lim.get("max_steps")
        if not _is_int(ms) or ms < 1:
            problems.append("- limits.max_steps must be a positive integer")
        else:
            max_steps = ms
        mc = lim.get("max_cost_usd")
        if mc is not None:
            if not _is_num(mc) or mc < 0:
                problems.append("- limits.max_cost_usd must be a non-negative number")
            else:
                max_cost = float(mc)
        mtc = lim.get("max_tool_calls_per_step")
        if mtc is not None:
            if not _is_int(mtc) or mtc < 1:
                problems.append("- limits.max_tool_calls_per_step must be a positive integer")
            else:
                max_calls = mtc
        mcc = lim.get("max_context_chars")
        if mcc is not None:
            if not _is_int(mcc) or mcc < 100:
                problems.append("- limits.max_context_chars must be an integer >= 100")
            else:
                max_context = mcc

    # --- cross-field rule ---------------------------------------------------
    if max_cost is not None and (in_price is None or out_price is None):
        problems.append(
            "- limits.max_cost_usd requires BOTH provider.usd_per_1k_input and "
            "provider.usd_per_1k_output (cost cannot be tracked without pricing)"
        )

    # --- log_truncate_chars -------------------------------------------------
    ltc = data.get("log_truncate_chars", 2000)
    if not _is_int(ltc) or ltc < 100:
        problems.append("- log_truncate_chars must be an integer >= 100")
        ltc = 2000
    elif ltc >= LOG_TRUNCATE_MAX:
        problems.append(
            f"- log_truncate_chars must be an integer >= 100 and below the "
            f"activity-log tail window ({LOG_TRUNCATE_MAX}); at or above it a "
            f"single capped string can wedge the log's tail scan")
        ltc = 2000

    # --- config-tamper guard (FIXLIST item 2) -----------------------------
    # The gate's own constitution must live OUTSIDE the agent's sandbox:
    # one approved file_write inside identity.home could otherwise swap the
    # rules that are re-loaded on every run/resume.
    if home_raw is not None:
        home_resolved = _resolve(base, home_raw)
        if policy_path is not None and _inside(policy_path, home_resolved):
            problems.append(
                f"- policy file {policy_path} resolves inside identity.home "
                f"{home_resolved}: the gate's config must stay outside the "
                f"agent's sandbox (config-tamper protection)")

    if problems:
        raise ManifestError("invalid manifest:\n" + "\n".join(problems))

    identity = Identity(name=name, role=role, home=_resolve(base, home_raw))
    provider = ProviderConfig(
        type=str(ptype), base_url=base_url, api_key_env=api_key_env, model=model,
        usd_per_1k_input=float(in_price) if in_price is not None else None,
        usd_per_1k_output=float(out_price) if out_price is not None else None,
        timeout_s=float(timeout_s), script_path=script_path,
    )
    limits = Limits(max_steps=max_steps, max_cost_usd=max_cost,
                    max_tool_calls_per_step=max_calls,
                    max_context_chars=max_context)
    return Manifest(
        identity=identity, goal=goal, policy_path=policy_path,
        inline_policy=inline_policy, credential_env_vars=tuple(creds),
        provider=provider, limits=limits, log_truncate_chars=ltc,
    )
