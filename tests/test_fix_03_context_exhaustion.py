"""FIXLIST item 3 (must-fix): context exhaustion.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. Contract pinned
from the deliberation (turn-06-glm new finding 2, endorsed in the memo):

- a new terminal run status ``stopped_context`` (added to RUN_STATUSES)
  is entered when the provider fails with a context-length error (the
  OpenAI-compatible family's "maximum context length" 400), instead of the
  generic ``failed`` -> resume -> failed-forever loop;
- a configurable near-limit estimate (pinned manifest field:
  ``limits.max_context_chars``, estimated over the message contents) trips
  the same status BEFORE the next model call, so the run stops instead of
  burning a provider round-trip it knows will fail;
- resume surfaces ``stopped_context`` clearly (it does not masquerade as a
  generic failure while the history is unchanged).
"""

import unittest
from pathlib import Path

import support
from fullstop.manifest import manifest_from_dict
from fullstop.provider import ModelReply, ProviderError, Usage
from fullstop.state import checkpoint_path, load_checkpoint
from fullstop.types import RUN_STATUSES

CONTEXT_400 = ProviderError(
    "http 400: This model's maximum context length is 16385 tokens. "
    "However, you requested 20000 tokens. Please reduce the length of the "
    "messages.")


class ContextErrorProvider:
    """Always fails with a realistic context-length provider error."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        raise CONTEXT_400


class ProbeProvider:
    """Counts model calls; must never be called by the near-limit guard."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return ModelReply("nothing to do", Usage(0, 0, True))


class ContextExhaustionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def test_status_vocabulary_gains_stopped_context(self):
        self.assertIn("stopped_context", RUN_STATUSES)

    def test_context_length_provider_error_stops_in_stopped_context(self):
        provider = ContextErrorProvider()
        loop = support.build_loop(self.home, [], provider=provider)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "stopped_context",
                         f"got {state.status!r} with failure "
                         f"{state.failure!r}")
        persisted = load_checkpoint(checkpoint_path(self.home))
        self.assertEqual(persisted.status, "stopped_context")

    def test_near_limit_estimate_stops_before_provider_call(self):
        # Build through the validator: the pinned field is
        # limits.max_context_chars (estimated context size in characters).
        script = support.write_script(self.home / ".fullstop", ["nothing"])
        data = {
            "identity": {"name": "t", "role": "t",
                         "home": str(self.home)},
            "goal": "g",
            "policy": {},
            "provider": {"type": "scripted",
                         "script_path": str(script)},
            "limits": {"max_steps": 5, "max_context_chars": 400},
        }
        manifest = manifest_from_dict(data, base_dir=self.tmp)
        provider = ProbeProvider()
        loop = support.build_loop(self.home, [], manifest=manifest,
                                  provider=provider)
        state = loop.run(loop.new_state())
        self.assertEqual(
            provider.calls, 0,
            "the near-limit estimate must trip BEFORE the model is called")
        self.assertEqual(state.status, "stopped_context")
        entries = support.read_events(self.home)
        trips = support.events_of(entries, "guard_trip")
        self.assertTrue(trips, "a guard event must record the context stop")
        self.assertIn("context", trips[0].get("kind", ""))

    def test_resume_surfaces_stopped_context_not_generic_failure(self):
        provider = ContextErrorProvider()
        loop1 = support.build_loop(self.home, [], provider=provider)
        loop1.run(loop1.new_state())
        loaded = load_checkpoint(checkpoint_path(self.home))
        self.assertEqual(loaded.status, "stopped_context")
        # Operator resumes exactly as the README teaches; the history is
        # unchanged, so the run must stop in stopped_context again — visibly,
        # not as an opaque "failed".
        provider2 = ContextErrorProvider()
        loop2 = support.build_loop(self.home, [], provider=provider2)
        state = loop2.run(loaded)
        self.assertEqual(state.status, "stopped_context",
                         f"resume must surface the context exhaustion, got "
                         f"{state.status!r}")


if __name__ == "__main__":
    unittest.main()
