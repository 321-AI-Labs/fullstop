# fullstop

**Your agent. Your machine. Full stop.**

A self-hosted, model-agnostic, **zero-dependency** Python runtime for always-on
agents: each agent has a persistent workspace, an identity with scoped
credentials, a policy gate that reviews every consequential action, and a
tamper-evident append-only activity log.

The free, open-source counterpart to OpenAI's Dots announcement (DevDay 2026,
Sept 29), built the week it was announced. `fullstop` is the final name
(verified free on PyPI 2026-09-30); the project was developed under the
working name `hearth`, which is why early git history says so. No OpenAI
marks are used in the repo, package, or CLI names.

- Runtime: Python 3.11+, **stdlib only** (no third-party imports anywhere —
  proven by `tests/test_imports.py`)
- `pathlib` everywhere, no shellisms; the suite runs green on Windows (CI
  is configured to add `ubuntu-latest`)
- Synchronous only; JSON everywhere, no YAML

## What it is, honestly

An agent that keeps working toward a goal across sessions: you give it a goal
and a manifest, it runs a plan → act → observe loop against an
OpenAI-compatible model API, checkpoints its state as JSON after every step,
and can resume after a crash — including mid-step. The crash-mid-run resume
without double execution is demonstrated by
`tests/test_fix_01_crash_resume.py`, not just guard-stop resumes.

It is **not**: a hosted cloud product; a real browser in v0.1 (`web_fetch`
only — the `browser` tool interface is defined but unimplemented, and saying
so is enforced by `tests/test_tools.py::BrowserToolTests`); a multi-agent
orchestrator; a plugin marketplace; streaming output or a desktop GUI (the
loopback activity view documented below is a local dashboard served to your
browser, not either); wired to
any non-OpenAI-compatible provider protocol (Anthropic native etc. — only
OpenAI-compatible chat-completions endpoints, plus the scripted mock).

## fullstop vs Dots — as announced

OpenAI announced Dots and Specialist Dots at DevDay 2026 (Sept 29). The
comparison below is scoped to the capabilities **as announced** that day —
not to press speculation — and is dated for exactly that reason: OpenAI may
have shipped more since. OpenAI and Dots are trademarks of OpenAI; fullstop
is an independent open-source project with no affiliation. What fullstop
**has**, **has not**, and **will not** have:

| Capability | fullstop v0.1 | Dots (as announced 2026-09-29) |
| --- | --- | --- |
| Hosting | self-hosted, your machine | OpenAI cloud |
| Model | any endpoint that speaks the OpenAI chat-completions API (unit-tested against scripted mock endpoints; you supply the base URL; live endpoints untested — see Limits) | OpenAI models |
| Always-on loop, checkpoints, resume | has — incl. crash-mid-run with no double execution (`tests/test_fix_01_crash_resume.py`) | has (managed) |
| Policy gate over every consequential action | has — code between model and tools, default-deny (`tests/test_policy_gate.py`) | has (product-managed review) |
| Human approval showing the full request | has — at the console (`tests/test_fix_06_approval_display.py`) | has (in-product) |
| Tamper-evident activity log you can read and verify | has — with rotation (`tests/test_fix_08_activity_log_rotation.py`) | not disclosed |
| Config integrity anchored in the log | has — `config_loaded` hashes; resume refuses swapped gate rules (`tests/test_fix_02_config_tamper.py`) | not disclosed |
| Browser automation | **has not** — v0.1 is `web_fetch` only, redirect hops re-validated per hop (`tests/test_fix_05_redirect_chain.py`) | has (built-in) |
| Multi-agent orchestration / specialist dots | **has not** | has |
| Streaming output | **has not** | has |

fullstop **will not** have — permanent non-goals, not a todo list: cloud
hosting; a plugin marketplace; non-OpenAI-compatible provider protocols
(Anthropic native etc.); multi-agent orchestration.

fullstop **has not** yet, and may gain later: real browser automation;
streaming; message-history compaction (context exhaustion stops the run
honestly — see Limits below).

## Quickstart (keyless, exactly ONE approval prompt)

From the repo root:

```bash
python -m fullstop run --manifest examples/scripted-demo.json
```

The scripted demo needs **no API key and no network**. It performs, in order:

1. a `note` write to `memory.md` — pre-approved by the demo policy, **no prompt**;
2. a pre-approved `notes/intro.md` write + read — **no prompt**;
3. a `summary.md` write that matches nothing pre-approved — **THE one prompt**:
   - answer `y` → the write executes and `summary.md` appears;
   - answer `n` (or just Enter) → the write is denied, logged, and **the run
     still completes**;
   - note: on a NON-interactive console (piped stdin) no one can answer, so
     the run pauses in `stopped_approval` instead — resume it from a real
     terminal (`tests/test_fix_10_unattended_stop.py`);
4. a glob-disguised escape write `notes/../../escape.txt` — the glob
   `notes/**` pre-approves it at the gate (no prompt), and the **sandbox
   blocks it** — you watch the backstop work without being asked;
5. a final summary turn — run completes.

Then inspect the evidence:

```bash
python -m fullstop log --manifest examples/scripted-demo.json --verify
python -m fullstop status --manifest examples/scripted-demo.json
```

The quickstart's exact behavior (exactly one prompt, the artifact set, the
sandbox block, the verified hash chain) is asserted by
`tests/test_examples.py`, which loads the shipped files from disk.

## The example manifests

| Manifest | What it demonstrates | Prompts |
| --- | --- | --- |
| `examples/scripted-demo.json` | the full loop: pre-approved writes, one approval, a blocked escape | exactly one |
| `examples/research-readonly.json` | a read-only specialist: listings are free, `notes/**` and `memory.md` are protected, so both write attempts (`file_write`, `note`) are hard-denied with no human present — the workspace ends untouched | zero |
| `examples/writer-approval.json` | an approval-gated writer: `drafts/**` writes freely, the `report.md` write and the memory note each ask once (answer `y`, then `n`), and a `secret-*.txt` write is unapprovable at any price | exactly two |
| `examples/research-assistant.json` | the real-provider template (needs `FULLSTOP_API_KEY` and your model id; validated by load in `tests/test_examples.py`) | — |

```bash
python -m fullstop run --manifest examples/research-readonly.json
python -m fullstop run --manifest examples/writer-approval.json
```

The first completes with zero prompts on any console. The second needs a
human for its two prompts; where no one can answer, the same attended-only
rules as the quickstart apply — a genuinely non-tty console pauses in
`stopped_approval` for a later `resume`. That unattended stop is tested
generically (`tests/test_fix_10_unattended_stop.py`), not against this
example — in the suite the writer-approval run is exercised with an
approver armed. Both runs' exact behavior — prompt counts, decision trail,
artifact set, verified chain — is asserted by `tests/test_examples.py`.

Any run can override its manifest's goal for that one run with `--goal`
(`python -m fullstop run --manifest examples/research-readonly.json --goal
"Inventory only"`); the override is applied before the loop — and thus the
system prompt — is built (`tests/test_fix_04_goal_override.py`).

## Resuming, editing the config, and `--allow-config-change`

`fullstop resume` continues from the checkpoint. Two facts govern every
resume:

- **Guard-stopped runs are resumable.** When a run stops at a limit
  (`stopped_max_steps`, `stopped_max_cost`, `stopped_context`,
  `stopped_approval`), the operator edits the manifest — the canonical
  case is to raise `max_steps` (or lift `max_context_chars` / remove a
  deny rule) — and resumes from there
  (`tests/test_agent_loop.py`, `tests/test_fix_10_unattended_stop.py`).
- **Resume refuses a changed config by default.** Every start and resume
  appends a `config_loaded` event with sha256 hashes of the manifest and
  policy bytes; `resume` compares against the last one and refuses on a
  mismatch, because one approved write must never silently swap the gate's
  rules (`tests/test_fix_02_config_tamper.py`).

Editing the manifest to raise `max_steps` changes the manifest bytes, so
the standard resume-with-edit flow is:

```bash
python -m fullstop resume --manifest <path.json> --allow-config-change
```

`--allow-config-change` is the explicit operator override: you assert the
config change is yours and intentional, and the new `config_loaded` event
anchors the run to the edited rules from that resume onward.

**Failed runs have no supported retry.** Only guard-stopped
(`stopped_*`) and crash-mid-run (`running`) checkpoints continue;
`completed`, `failed`, and `script_exhausted` runs are terminal — `resume`
on them does nothing except re-log and exit (nonzero for `failed`). There
is no supported retry of a failed run; start a new run (`run`) instead.

## The activity view (loopback dashboard)

`python -m fullstop ui --manifest <path.json>` serves a dashboard for that
manifest's workspace and opens it in a browser (`--no-browser` prints the
URL instead). `run --ui` and `resume --ui` serve the same dashboard while a
run executes: the run stays foreground and the dashboard lives on daemon
threads, polling the checkpoint and log files the loop already writes; the
server never imports the agent loop (`tests/test_ui_cli.py`).

What it shows:

- the current run: goal, status, steps, cost, tokens, and the checkpointed
  failure line if any;
- the activity log as written: narration (model replies), tool calls, gate
  decisions, approvals, sandbox blocks, guard trips, run end
  (`tests/test_ui_model.py`);
- a history panel of the runs recorded in this workspace, and a verify
  button that re-checks the hash chain and reports the first broken entry
  (`tests/test_ui_server.py`);
- approval cards. With `--ui`, an APPROVAL_REQUIRED decision renders the
  complete request (the full `render_request` output, never a summary) in
  the browser. Approvals use a decision-file protocol: the pending card is
  written to a rendezvous directory under the OS temp dir, deliberately
  OUTSIDE the workspace sandbox so the agent's own file tools can never
  reach it, let alone forge an approval; each prompt carries a 64-bit
  random id bound into both filenames; a wrong-id or forged decision file
  is ignored; and on timeout (default 300 s, `--approval-timeout`) the run
  fails closed into `stopped_approval`, exactly like an unattended console
  (`tests/test_ui_approver.py`, `tests/test_ui_cli.py`);
- a new-run wizard: builds ONE manifest file with an inline policy,
  validated by the same strict validators the runtime uses (errors surface
  field-level and verbatim), saves to new files only (an existing path is
  refused and never modified; the no-clobber write is atomic), and carries
  credential env var NAMES only; the written bytes are scrubbed through
  `Redactor.from_env`, so an environment value can never be persisted even
  if pasted into a text field (`tests/test_ui_wizard.py`).

Trust boundaries, stated plainly:

- the server binds 127.0.0.1 only and there is NO host flag anywhere; a
  `--host` argument is rejected by the CLI parser
  (`tests/test_ui_server.py`);
- requests whose Host header is not this loopback origin are refused
  (DNS-rebinding guard), and every POST must carry the `X-Fullstop-UI`
  header that cross-site form posts cannot set (CSRF guard)
  (`tests/test_ui_server.py`);
- the dashboard deliberately carries NO token. This is a deliberate
  contrast with keysmith's token-gated dashboard, not an oversight: any
  process running as the local user can read the page and answer an
  approval card. If that is not acceptable on your machine, do not use
  `--ui`;
- the only writes the dashboard can perform are the decision file and,
  when explicitly asked through the wizard, one new manifest file; it
  holds no code path that opens checkpoints, manifests, or the log for
  writing (`tests/test_ui_server.py`).

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
  "limits":    {"max_steps": int, "max_cost_usd": num?, "max_tool_calls_per_step": int?,
                 "max_context_chars": int?},   // near-limit estimate -> stopped_context
  "log_truncate_chars": int?
}
```

`identity.home`, the policy path form, and `provider.script_path` all resolve
**relative to the manifest file's directory** (validated from any CWD by
`tests/test_manifest_policy.py`). `max_cost_usd` requires both pricing fields
(they are illustrative placeholders in `examples/research-assistant.json` —
set them to your provider's real prices).

**Model config note — the markers are load-bearing.** The
`<<<TOOL_CALL>>>` / `<<<END_TOOL_CALL>>>` fences are the only way a model can
act. A model that *mentions* a marker in prose (quoted or not) hijacks the
scan: by quote parity, the next real block surfaces as a loud
`malformed_call` error (`invalid JSON` / `unterminated`) instead of
executing, and case-mismatched markers (e.g. `<<<tool_call>>>`) are plain
prose — the reply completes silently with no call. When choosing or prompting
a model, tell it to **never echo the markers outside an actual call**
(`tests/test_fix_23_parser_stray_marker_cliffs.py` pins the exact cliffs).

## Policy schema (default-deny)

```jsonc
{
  "protected_paths": ["secrets/**", "*.pem", ".env"],   // + non-removable .fullstop/**
  "write_preapproved": ["notes/**", "memory.md"],
  "shell": {"allow": ["git",                       // bare name: ONLY the bare invocation
                      {"program": "git",             // constrained entry: program
                       "args": ["status"]}],         // + exact/pattern ("*") arguments
            "deny": ["rm"], "timeout_s": 30},
  "web": {"allow_domains": ["arxiv.org"], "deny_domains": [], "timeout_s": 10,
           "max_bytes": 1048576}
}
```

**The note-is-a-write law:** `note` is a WRITE, gated exactly like
`file_write` against `memory.md`. It is DENIED when that file is protected,
pre-approved when it is listed in `write_preapproved`, and otherwise
APPROVAL_REQUIRED. Operators who want frictionless memory put `"memory.md"` in
`write_preapproved` — of the four shipped example policies, `scripted-demo`
and `research-assistant` preapprove it, `writer-approval` routes the memory
note to human approval (it asks once), and `research-readonly` protects
`memory.md` outright so the note is hard-denied. Proven by
`tests/test_policy_gate.py`.

**The no-arbitrary-arguments law:** an allowlist entry pre-approves a
program ONLY together with its argument constraints — a bare string entry
pre-approves just the bare invocation, a `{"program", "args"}` entry
pre-approves exactly matching argument patterns; anything else routes to
human approval. Programs are matched as resolved absolute paths (PATH-only,
`.exe`-pinned on Windows; the workspace cwd is never searched, and
`.bat`/`.cmd` targets are refused outright). Proven by
`tests/test_fix_07_shell_allowlist.py`.

## The gate (the differentiator)

Every tool call is classified read-only vs consequential and decided by the
table in `fullstop/gate.py`:

| call | decision |
| --- | --- |
| `file_read` unprotected / `file_list` of an ordinary directory | ALLOW (read-only; protected entries such as `.fullstop/` are hidden from the listing) |
| `file_list` of a protected directory | **operator-approvable** |
| `file_read` protected (incl. `.fullstop/**`) | **operator-approvable** |
| `file_write` protected / denylisted shell / denylisted domain / `.fullstop` write | **hard DENY — approval never upgrades it** |
| `file_write` / `note` / `shell` / `web_fetch` / `browser` matching a pre-approval | ALLOW |
| everything else consequential | APPROVAL_REQUIRED → human prompt |

The gate matches the **literal** path spelling (a glob can pre-approve
`notes/../../escape.txt`); the sandbox chokepoint inside the tool is the
ordered backstop that kills it. Approvals only unlock APPROVAL_REQUIRED
outcomes; hard denials stay hard (`tests/test_policy_gate.py`). ALLOW decisions
carry a per-call token minted with a per-Gate secret; the tool registry
refuses to execute anything else (`fullstop/tools/base.py`), so a forged
decision from model output cannot reach a tool.

## Security model — and its limits

Model:

- **Sandbox**: all file tools pass through `fullstop/sandbox.py` — absolute,
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
- `web_fetch` has no JavaScript and no cookies. Redirects ARE followed, but
  every hop is re-validated against the full domain allow/deny predicate
  before it is requested (proven against a live loopback redirect chain in
  `tests/test_fix_05_redirect_chain.py`); the ordinary live-network path is
  otherwise exercised only by that test.
- **Context exhaustion**: message history grows without compaction in v0.1.
  When the provider rejects a call for context length, or the estimated
  context reaches `limits.max_context_chars`, the run stops in
  `stopped_context` — surfaced again on resume, honestly, rather than
  looping `failed` → resume → `failed`
  (`tests/test_fix_03_context_exhaustion.py`).
- **Attended-only approvals**: when a consequential action needs approval
  and no human can answer (no approver configured, or a non-tty console),
  the run pauses in `stopped_approval` instead of spinning to `max_steps`;
  it resumes once a human answers (`tests/test_fix_10_unattended_stop.py`).
- Live-model behavior (real internet model endpoints) is untested by the
  suite — the suite is keyless by design, and its only network I/O is
  loopback; the provider
  adapter is tested against a fake opener plus a loopback mock HTTP
  endpoint serving realistic error bodies (`tests/test_provider.py`,
  `tests/test_r2_02_provider_context_body.py`).
- The write-path equality rule intentionally **fails closed** on
  legal-but-unusual Windows spellings whose realpath differs from the literal
  (8.3 short names, trailing dots and spaces). Blocking them is deliberate;
  do not "repair" this.
- Protected **reads** are operator-approvable and, once approved, execute;
  protected **writes** are unapprovable, full stop.

## Deliberate deviation from the charter

`ScriptedModel` ships **in the package** (`fullstop/provider.py`), not only in
the test suite as CHARTER.md:15 imagined, because the keyless quickstart and
the smoke script need it. Tests import it from there. This is the only stated
deviation.

## Development

```bash
python -m unittest discover -s tests   # the whole suite: zero keys, loopback-only tests
python scripts/smoke.py                # DoD#2 smoke (keyless, socketless)
```

CI (`.github/workflows/ci.yml`) is configured to run the same suite on
`ubuntu-latest` and `windows-latest` on every push — with no install step,
because the repo is stdlib-only.

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
| Read-only example: zero prompts; both writes hard-denied; workspace untouched | `tests/test_examples.py` |
| Approval-gated writer example: exactly two prompts; protected write unapprovable | `tests/test_examples.py` |
| Quickstart: exactly one prompt, sandbox block, verified chain | `tests/test_examples.py` |
| Manifest/policy validation incl. relative resolution | `tests/test_manifest_policy.py` |
| Crash-mid-run resume: no double execution, no re-prompt of approved calls | `tests/test_fix_01_crash_resume.py` |
| Config anchored: in-sandbox config refused; hash mismatch blocks resume | `tests/test_fix_02_config_tamper.py` |
| Context exhaustion stops in `stopped_context` | `tests/test_fix_03_context_exhaustion.py` |
| `--goal` override reaches the system prompt | `tests/test_fix_04_goal_override.py` |
| Redirect hops re-validated per hop | `tests/test_fix_05_redirect_chain.py` |
| Approver prompts show the complete request | `tests/test_fix_06_approval_display.py` |
| Shell allowlist constrains arguments; PATH-resolved programs; batch refused | `tests/test_fix_07_shell_allowlist.py` |
| Log appends never re-read the file; rotation keeps the chain verifiable across segments | `tests/test_fix_08_activity_log_rotation.py` |
| `file_read` caps the read itself | `tests/test_fix_09_file_read_cap.py` |
| Unattended approval pauses in `stopped_approval` | `tests/test_fix_10_unattended_stop.py` |
| Redactor reads credentials at call time | `tests/test_fix_11_redactor_call_time.py` |
| `note` write protected like `file_write` (symlink-safe target) | `tests/test_fix_12_note_symlink.py` |
| `file_list` hides protected metadata without approval | `tests/test_fix_13_file_list_protected.py` |
| Approval-gated `web_fetch` logged as consequential | `tests/test_fix_14_gate_classification_label.py` |
| Tool output delimited per session (no forged observations) | `tests/test_fix_15_observation_delimiters.py` |
| `log_truncate_chars` reaches the activity log | `tests/test_fix_16_log_truncate_wiring.py` |
| README honesty (comparison vs announced Dots capabilities, non-goals, limits) | `tests/test_fix_17_19_readme_honesty.py` |
| Crash-resume replay-dedup holds for oversized and redacted args (raw-args digest) | `tests/test_r2_01_replay_dedup_keys.py` |
| Provider error bodies carried (size-capped); context 400 -> `stopped_context` via a mock HTTP endpoint | `tests/test_r2_02_provider_context_body.py` |
| Oversize log lines never wedge the tail scan; `log_truncate_chars` bounded below the tail window | `tests/test_r2_03_log_oversize_wedge.py` |
| `--allow-config-change` and the resume-with-edit flow documented | `tests/test_r2_04_readme_allow_config_change.py` |
| Error observations delimited per session, capped like ok-output | `tests/test_r2_05_error_observation_frame.py` |
| Resume of a pre-v0.1.2 system-only checkpoint seeds the goal from the persisted goal; no double seed | `tests/test_fix_22_resume_seed_guard.py` |
| Stray-marker cliffs pinned: bare mention → malformed + later block parses; quoted mention → unterminated; forged block inside an args string inert | `tests/test_fix_23_parser_stray_marker_cliffs.py` |
| Activity view: loopback-only bind, no host flag, Host allowlist + X-Fullstop-UI POST guards, decision-file-only write surface | `tests/test_ui_server.py` |
| Browser approvals: decision-file protocol outside the sandbox, full request on the card, forged id ignored, timeout → `stopped_approval` | `tests/test_ui_approver.py` |
| `run --ui` / `resume --ui` end-to-end over real loopback HTTP; `ui` subcommand serves until stopped | `tests/test_ui_cli.py` |
| UI read model: snapshot/history views and chain verify over the log | `tests/test_ui_model.py` |
| Wizard: real strict validators, verbatim field errors, new-file-only atomic save, env values never persisted | `tests/test_ui_wizard.py` |
| Front-end naming law (no prose in the assets) and inline-script breakout guard | `tests/test_ui_assets.py` |

## License

MIT — see `LICENSE`.
