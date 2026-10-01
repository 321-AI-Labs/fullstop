# Changelog

## v0.2.1

- CI platform portability: the suite was red on GitHub's runners (both jobs,
  first CI runs) while green on the Windows dev machine it was written on.
  `sandbox.py` gains `sandbox_rel(root, target)`, which takes the in-sandbox
  rel against the REALPATH'd root; `file_read`, `file_write`, and `note` now
  use it. An aliased root spelling — GitHub's Windows runners expose TEMP as
  the DOS 8.3 short name (`C:\Users\RUNNER~1\...`); a symlinked root is the
  same shape — previously made every rel crawl out as `../..` garbage, so
  the write-equality rule rejected every write/note on the runner (14
  windows-job errors/failures, one root cause) and the alias-scoped
  protected-read check never matched. Containment in `resolve_in_sandbox`
  (which already realpaths both sides) is unchanged.
- `file_write` rejects trailing-dot/space path components BEFORE resolution,
  on every platform. The equality rule already failed closed on those
  spellings where realpath collapses them (Windows); on Linux `x.md.` is a
  distinct legal filename, the write SUCCEEDED (creating a new file, the
  original untouched), and the documented fail-closed promise silently did
  not hold — the ubuntu job's one failure. Nothing is weakened: this
  restores the documented semantics cross-platform, on any path component.

## v0.2.0

- The loopback activity view: `python -m fullstop ui`, plus `run --ui` and
  `resume --ui`. A stdlib-served dashboard (127.0.0.1 only, deliberately no
  token; see the README's trust-boundary disclosure) showing the
  hash-chained activity log, run history with chain verify, browser
  approval cards over the decision-file protocol, and a new-run manifest
  wizard (inline policy, strict validators, new-file-only save).
- Review-fold fixes: approval-id docstrings corrected to 64 bits; every
  user-visible literal moved into `ui_strings.py`; a script-breakout guard
  on the injected strings blob; `pending_exists` is expiry-aware so an
  expired card earns the 404; the wizard save is atomically no-clobber
  (`os.link`), so a file created between check and write is never
  overwritten.

## v0.1.2

- Fences are recognized **anywhere** in a reply, not as whole lines. Found
  live: models sometimes glue `<<<TOOL_CALL>>>` directly onto prose, and the
  line-anchored match silently read those replies as no-call completions (a
  run could "complete" with the goal undone). Markers inside JSON string
  values stay inert.
- **Model-visible error wording changed** with the new scanner: a reply that
  mentions a stray `<<<TOOL_CALL>>>` in prose now surfaces as
  `invalid JSON in tool call block` (even quote parity to the next close
  marker) or `unterminated tool call block` (odd parity) where the
  line-anchored parser parsed the real blocks cleanly, and — in the other
  direction — a close marker glued inside a block's JSON string no longer
  splits the body into an `invalid JSON` error. The error text is fed back
  to the model as the `malformed_call` observation; prompts tuned to the
  v0.1.1 error shapes may need updating.
- Fresh runs seed the goal as an opening **user** turn (some
  OpenAI-compatible endpoints — e.g. Z.ai — reject system-only message
  lists with HTTP 400 code 1214); `resume` now applies the same seed to
  pre-v0.1.2 system-only checkpoints, from the persisted checkpoint goal.
