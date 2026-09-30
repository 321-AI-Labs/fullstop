# Changelog

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
