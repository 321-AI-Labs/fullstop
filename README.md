# hearth (working name — RENAME-PENDING-OWNER)

A self-hosted, model-agnostic, **zero-dependency** Python runtime for always-on
agents: each agent has a persistent workspace, an identity with scoped
credentials, a policy gate that reviews every consequential action, and a
tamper-evident append-only activity log.

The open counterpart to OpenAI's Dots announcement (DevDay 2026, Sept 29),
built the week it was announced. `hearth` is a working name; the owner will
pick the final name (candidates in the charter: hearth · ember · watchstander ·
nightwatch · open-agent-hearth). No OpenAI marks are used in the repo, package,
or CLI names.

- Runtime: Python 3.11+, **stdlib only** (no third-party imports anywhere —
  proven by `tests/test_imports.py`)
- Windows-honest and POSIX-honest: `pathlib` everywhere, no shellisms
- Synchronous only; JSON everywhere, no YAML

## What it is, honestly

An agent that keeps working toward a goal across sessions: you give it a goal
and a manifest, it runs a plan → act → observe loop against any
OpenAI-compatible model API, checkpoints its state as JSON after every step,
and can resume after a crash or a reboot.

It is **not**: a hosted cloud product; a real browser in v0.1 (`web_fetch`
only — the `browser` tool interface is defined but unimplemented, and saying
so is enforced by `tests/test_tools.py::BrowserToolTests`); a multi-agent
orchestrator; a streaming or GUI anything.

## Quickstart (keyless, under 10 minutes, exactly ONE approval prompt)

```bash
cd repo-root
python -m hearth run --manifest examples/scripted-demo.json
```

The scripted demo needs **no API key and no network**. It performs, in order:

1. a `note` write to `memory.md` — pre-approved by the demo policy, **no prompt**;
2. a pre-approved `notes/intro.md` write + read — **no prompt**;
3. a `summary.md` write that matches nothing pre-approved — **THE one prompt**:
   - answer `y` → the write executes and `summary.md` appears;
   - answer `n` (or just Enter) → the write is denied, logged, and **the run
     still completes**;
4. a glob-disguised escape write `notes/../../escape.txt` — the glob
   `notes/**` pre-approves it at the gate (no prompt), and the **sandbox
   blocks it** — you watch the backstop work without being asked;
5. a final summary turn — run completes.

Then inspect the evidence:

```bash
python -m hearth log --manifest examples/scripted-demo.json --verify
python -m hearth status --manifest examples/scripted-demo.json
```

The quickstart's exact behavior (exactly one prompt, the artifact set, the
sandbox block, the verified hash chain) is asserted by
`tests/test_examples.py`, which loads the shipped files from disk.

## Manifest schema (strict; unknown keys rejected)

```jsonc
{
  "identity":  {"name": str, "role": str, "home": str},   // home = workspace dir
  "goal":      str,
  "policy":    "path.json" | { "inline policy object" },
  "credentials": ["ENV_VAR_NAME", ...],                   // optional; NAMES only
  "provider":  {"type": "openai_compat", "base_url": str, "api_key_env": str,
                 "model": str, "usd_per_1k_input": num?, "usd_per_1k_output": num?,
                 "timeout_s": num?}
             |  {"type": "scripted", "script_path": str},
  "limits":    {"max_steps": int, "max_cost_usd": num?, "max_tool_calls_per_step": int?},
  "log_truncate_chars": int?
}
```

`identity.home`, the policy path form, and `provider.script_path` all resolve
**relative to the manifest file's directory** (validated from any CWD by
`tests/test_manifest_policy.py`). `max_cost_usd` requires both pricing fields
(they are illustrative placeholders in `examples/research-assistant.json` —
set them to your provider's real prices).

## Policy schema (default-deny)

```jsonc
{
  "protected_paths": ["secrets/**", "*.pem", ".env"],   // + non-removable .hearth/**
  "write_preapproved": ["notes/**", "memory.md"],
  "shell": {"allow": ["ls", "git"], "deny": ["rm"], "timeout_s": 30},
  "web": {"allow_domains": ["arxiv.org"], "deny_domains": [], "timeout_s": 10,
           "max_bytes": 1048576}
}
```

**The note-is-a-write law:** `note` is a WRITE, gated exactly like
`file_write` against `memory.md`. It is DENIED when that file is protected,
pre-approved when it is listed in `write_preapproved`, and otherwise
APPROVAL_REQUIRED. Operators who want frictionless memory put `"memory.md"` in
`write_preapproved` — both shipped example policies do. Proven by
`tests/test_policy_gate.py`.

## The gate (the differentiator)

Every tool call is classified read-only vs consequential and decided by the
table in `hearth/gate.py`:

| call | decision |
| --- | --- |
| `file_read` unprotected / `file_list` | ALLOW (read-only) |
| `file_read` protected (incl. `.hearth/**`) | **operator-approvable** |
| `file_write` protected / denylisted shell / denylisted domain / `.hearth` write | **hard DENY — approval never upgrades it** |
| `file_write` / `note` / `shell` / `web_fetch` / `browser` matching a pre-approval | ALLOW |
| everything else consequential | APPROVAL_REQUIRED → human prompt |

The gate matches the **literal** path spelling (a glob can pre-approve
`notes/../../escape.txt`); the sandbox chokepoint inside the tool is the
ordered backstop that kills it. Approvals only unlock APPROVAL_REQUIRED
outcomes; hard denials stay hard (`tests/test_policy_gate.py`). ALLOW decisions
carry a per-call token minted with a per-Gate secret; the tool registry
refuses to execute anything else (`hearth/tools/base.py`), so a forged
decision from model output cannot reach a tool.

## Security model — and its limits

Model:

- **Sandbox**: all file tools pass through `hearth/sandbox.py` — absolute,
  drive/UNC/ADS/colon, NUL, and `..` spellings are rejected; realpath +
  commonpath containment; symlink aliases are blocked
  (`tests/test_sandbox.py`).
- **Anti-aliasing**: writes additionally require resolved == literal path, so
  symlinked/short-name/trailing-dot redirects of writes fail closed
  (`tests/test_tools.py`).
- **Credentials**: manifests carry env var NAMES only; values are read at call
  time, scrubbed (`[REDACTED:NAME]`) at every write layer, and removed from
  child process environments (`tests/test_credentials.py`).
- **Tamper-evident log**: every line chains
  `sha256(prev:canonical_json(line))`; appending onto a tampered tail fails
  loudly (`tests/test_activity_log.py`). Tamper-evident, **not** tamper-proof —
  an attacker with write access to the file can rebuild the whole chain.

Limits, stated plainly ("not yet" — untested or unimplemented):

- Shell children inherit the environment **minus the credential names** — full
  environment isolation is not yet implemented.
- `web_fetch` has no JavaScript, no cookies, no redirects-beyond-urllib; the
  real-network path is exercised only by the injected-fake tests
  (`tests/test_tools.py`), so live fetch behavior is otherwise untested.
- Live-model behavior (real OpenAI-compatible endpoints) is untested by the
  suite — the suite is keyless and socketless by design; the provider adapter
  is tested against a fake opener (`tests/test_provider.py`).
- The write-path equality rule intentionally **fails closed** on
  legal-but-unusual Windows spellings whose realpath differs from the literal
  (8.3 short names, trailing dots and spaces). Blocking them is deliberate;
  do not "repair" this.
- Protected **reads** are operator-approvable and, once approved, execute;
  protected **writes** are unapprovable, full stop.

## Deliberate deviation from the charter

`ScriptedModel` ships **in the package** (`hearth/provider.py`), not only in
the test suite as CHARTER.md:15 imagined, because the keyless quickstart and
the smoke script need it. Tests import it from there. This is the only stated
deviation.

## Development

```bash
python -m unittest discover -s tests   # the whole suite: zero keys, zero network
python scripts/smoke.py                # DoD#2 smoke (keyless, socketless)
```

## Claim → demonstrating test

| Claim | Test |
| --- | --- |
| Zero third-party imports | `tests/test_imports.py` |
| Sandbox escapes blocked + logged | `tests/test_sandbox.py` |
| Credentials never logged/written | `tests/test_credentials.py` |
| Gate not bypassable by model output; approvals execute; hard denials stick | `tests/test_policy_gate.py` |
| Tamper-evident chained log | `tests/test_activity_log.py` |
| Strict protocol parsing | `tests/test_protocol.py` |
| Checkpoints atomic + scrubbed | `tests/test_state.py` |
| Tool semantics (incl. alias rules) | `tests/test_tools.py` |
| Provider adapter (key only in header) | `tests/test_provider.py` |
| Loop: guards, resume, event sequence | `tests/test_agent_loop.py` |
| CLI exit codes / JSON output | `tests/test_cli.py` |
| Shipped examples work as documented | `tests/test_examples.py` |
| Quickstart: exactly one prompt, sandbox block, verified chain | `tests/test_examples.py` |
| Manifest/policy validation incl. relative resolution | `tests/test_manifest_policy.py` |

## License

MIT — see `LICENSE`.
