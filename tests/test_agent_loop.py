"""Loop proof: full scripted run, checkpoint after every step, complete event
sequence; max_steps/max_cost trips; resume with history + script cursor;
approve/deny/no-approver (overlaps test_policy_gate by design); sandbox_block
and protected_target events during the loop; script_exhausted; token/cost
accounting."""

import unittest
from pathlib import Path

import support
from fullstop.agent import ScriptedApprover
from fullstop.manifest import Identity, Limits, Manifest, ProviderConfig
from fullstop.policy import Policy
from fullstop.provider import ModelReply, ProviderError, ScriptedModel, Usage
from fullstop.redact import Redactor
from fullstop.state import checkpoint_path, load_checkpoint
from fullstop.types import EVENTS, RUN_STATUSES

WRITE_NOTES = Policy(write_preapproved=("notes/**", "memory.md"))


class FakeProvider:
    """Deterministic provider with exact usage (not estimated)."""

    def __init__(self, replies, usage=None, exc=None):
        self.replies = list(replies)
        self.exc = exc
        self.usage = usage or Usage(0, 0, False)

    def complete(self, messages):
        if self.exc is not None:
            raise self.exc
        content = self.replies.pop(0)
        return ModelReply(content, Usage(self.usage.input_tokens,
                                         self.usage.output_tokens, False))


class FullRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp)

    def test_full_run_event_sequence_and_artifacts(self):
        replies = [
            support.call_block("note", {"text": "start"}) + "\n"
            + support.call_block("file_write",
                                 {"path": "notes/a.md", "content": "A"}),
            support.call_block("file_read", {"path": "notes/a.md"}),
            "nothing more to do",
        ]
        loop = support.build_loop(self.home, replies, policy=WRITE_NOTES)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.steps_done, 2)
        entries = support.read_events(self.home)
        names = [e["event"] for e in entries]
        self.assertEqual(names[0], "run_start")
        self.assertEqual(names[-1], "run_end")
        for expected in ("model_reply", "tool_call", "gate_decision",
                         "tool_result", "step", "checkpoint"):
            self.assertIn(expected, names)
        # every logged event name is in the vocabulary
        self.assertLessEqual(set(names), set(EVENTS))
        # two steps -> two step events; the plan's loop checkpoints after
        # every iteration that ran step(), including the completing turn
        self.assertEqual(names.count("step"), 2)
        self.assertEqual(names.count("checkpoint"), 3)
        # artifacts exist inside the workspace
        self.assertTrue((self.home / "memory.md").exists())
        self.assertTrue((self.home / "notes" / "a.md").exists())
        # final checkpoint persisted, chain intact
        self.assertEqual(load_checkpoint(checkpoint_path(self.home)).status,
                         "completed")

    def test_checkpoint_after_every_step(self):
        replies = [
            support.call_block("file_write",
                               {"path": "notes/1.md", "content": "1"}),
            support.call_block("file_write",
                               {"path": "notes/2.md", "content": "2"}),
            support.call_block("file_write",
                               {"path": "notes/3.md", "content": "3"}),
            "done",
        ]
        loop = support.build_loop(self.home, replies, policy=WRITE_NOTES)
        state = loop.run(loop.new_state())
        self.assertEqual(state.steps_done, 3)
        entries = support.read_events(self.home)
        # 3 steps + the completing turn (plan-literal: checkpoint every
        # iteration that ran step())
        self.assertEqual([e["event"] for e in entries].count("checkpoint"), 4)
        checkpoints = support.events_of(entries, "checkpoint")
        self.assertEqual([c["n"] for c in checkpoints], [1, 2, 3, 3])

    def test_max_steps_guard_trips(self):
        replies = [
            support.call_block("file_write",
                               {"path": f"notes/{i}.md", "content": "x"})
            for i in range(5)
        ] + ["never reached"]
        script = support.write_script(self.home / ".fullstop", replies)
        manifest = support.script_manifest(self.home, script, max_steps=2)
        loop = support.build_loop(self.home, replies, policy=WRITE_NOTES,
                                  manifest=manifest)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "stopped_max_steps")
        self.assertIn(state.status, RUN_STATUSES)
        entries = support.read_events(self.home)
        trips = support.events_of(entries, "guard_trip")
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["kind"], "max_steps")
        self.assertEqual(entries[-1]["event"], "run_end")

    def test_max_cost_guard_trips(self):
        manifest = Manifest(
            identity=Identity("t", "t", self.home),
            goal="g", policy_path=None, inline_policy=None,
            provider=ProviderConfig(type="openai_compat",
                                    base_url="https://x", api_key_env="K",
                                    model="m",
                                    usd_per_1k_input=1.0, usd_per_1k_output=1.0),
            limits=Limits(max_steps=10, max_cost_usd=100.0),
        )
        provider = FakeProvider(
            [support.call_block("file_write",
                                {"path": "notes/c.md", "content": "x"})] * 5,
            usage=Usage(100_000, 100_000, False))
        loop = support.build_loop(self.home, [], policy=WRITE_NOTES,
                                  manifest=manifest, provider=provider)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "stopped_max_cost")
        self.assertEqual(state.steps_done, 1)  # trip checked before step 2
        entries = support.read_events(self.home)
        trips = support.events_of(entries, "guard_trip")
        self.assertEqual(trips[0]["kind"], "max_cost_usd")

    def test_resume_continues_with_history_and_cursor(self):
        replies = [
            support.call_block("file_write",
                               {"path": "notes/1.md", "content": "1"}),
            support.call_block("file_write",
                               {"path": "notes/2.md", "content": "2"}),
            "all done",
        ]
        script = support.write_script(self.home / ".fullstop", replies)
        manifest = support.script_manifest(self.home, script, max_steps=1)
        loop1 = support.build_loop(self.home, replies, policy=WRITE_NOTES,
                                   manifest=manifest)
        state = loop1.run(loop1.new_state())
        self.assertEqual(state.status, "stopped_max_steps")
        self.assertEqual(state.script_cursor, 1)

        loaded = load_checkpoint(checkpoint_path(self.home))
        # The operator raises max_steps and resumes.
        manifest2 = support.script_manifest(self.home, script, max_steps=5)
        provider2 = ScriptedModel.from_json_file(script,
                                                 start_cursor=loaded.script_cursor)
        loop2 = support.build_loop(self.home, replies, policy=WRITE_NOTES,
                                   provider=provider2, manifest=manifest2)
        state2 = loop2.run(loaded)
        self.assertEqual(state2.status, "completed")
        self.assertTrue((self.home / "notes" / "2.md").exists())
        entries = support.read_events(self.home)
        self.assertEqual(entries[0]["event"], "run_start")
        self.assertIn("resume", [e["event"] for e in entries])
        self.assertEqual(entries[-1]["event"], "run_end")

    def test_sandbox_block_event_during_loop(self):
        # Even an OPERATOR-APPROVED escape is blocked by the sandbox backstop.
        loop = support.build_loop(
            self.home,
            [support.call_block("file_write",
                                {"path": "../escape.txt", "content": "x"}),
             "done"],
            policy=Policy(), approver=ScriptedApprover([True]))
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed")  # the loop continues
        entries = support.read_events(self.home)
        blocks = support.events_of(entries, "sandbox_block")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["tool"], "file_write")
        self.assertIn("traversal", blocks[0]["detail"])
        results = support.tool_results(entries)
        self.assertEqual(results[0]["error_code"], "sandbox_escape")

    def test_protected_target_event_during_loop_via_symlink_alias(self):
        protected = self.home / ".fullstop" / "crown.txt"
        protected.write_text("jewels", encoding="utf-8")
        link = self.home / "alias.md"
        support.try_symlink(self, protected, link, "loop-alias-protected")
        loop = support.build_loop(
            self.home,
            [support.call_block("file_read", {"path": "alias.md"}), "done"],
            policy=Policy(), approver=ScriptedApprover([]))
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed")
        entries = support.read_events(self.home)
        results = support.tool_results(entries)
        self.assertEqual(results[0]["error_code"], "protected_target")
        blocks = support.events_of(entries, "sandbox_block")
        self.assertEqual(len(blocks), 1)

    def test_script_exhausted(self):
        loop = support.build_loop(
            self.home,
            [support.call_block("file_write",
                                {"path": "notes/x.md", "content": "x"})],
            policy=WRITE_NOTES)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "script_exhausted")
        entries = support.read_events(self.home)
        self.assertEqual(entries[-1]["event"], "run_end")
        self.assertEqual(entries[-1]["status"], "script_exhausted")

    def test_provider_error_fails_the_run_with_scrubbed_text(self):
        exc = ProviderError("env var FULLSTOP_SECRET_X not set")
        loop = support.build_loop(self.home, [], policy=WRITE_NOTES,
                                  provider=FakeProvider([], exc=exc))
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "failed")
        self.assertIn("env var FULLSTOP_SECRET_X not set", state.failure)

    def test_token_and_cost_accounting(self):
        manifest = Manifest(
            identity=Identity("t", "t", self.home),
            goal="g", policy_path=None, inline_policy=None,
            provider=ProviderConfig(type="openai_compat",
                                    base_url="https://x", api_key_env="K",
                                    model="m", usd_per_1k_input=0.5,
                                    usd_per_1k_output=2.0),
            limits=Limits(max_steps=5),
        )
        provider = FakeProvider(
            [support.call_block("file_write",
                                {"path": "notes/a.md", "content": "x"}),
             "done"],
            usage=Usage(100, 50, False))
        loop = support.build_loop(self.home, [], policy=WRITE_NOTES,
                                  manifest=manifest, provider=provider)
        state = loop.run(loop.new_state())
        self.assertEqual(state.tokens_in, 200)   # 2 model turns x 100
        self.assertEqual(state.tokens_out, 100)  # 2 x 50
        # per turn: 100/1000*0.5 + 50/1000*2.0 = 0.15
        self.assertAlmostEqual(state.cost_usd, 0.30)

    def test_no_approver_and_denial_paths(self):
        # v0.1.1 (FIXLIST item 10): an approval demand no human can hear
        # (no approver configured) pauses the run cleanly in
        # stopped_approval instead of spinning denials to max_steps.
        loop = support.build_loop(
            self.home,
            [support.call_block("file_write",
                                {"path": "unpre.md", "content": "x"}), "done"],
            policy=WRITE_NOTES, approver=None)
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "stopped_approval")
        self.assertLessEqual(state.steps_done, 1)
        results = support.tool_results(support.read_events(self.home))
        self.assertEqual(results[0]["error_code"], "denied_by_operator")
        self.assertEqual(results[0]["error"], "no approver configured")
        self.assertFalse((self.home / "unpre.md").exists())
        # A PRESENT human denying still just denies that one call.
        loop2 = support.build_loop(
            self.home,
            [support.call_block("file_write",
                                {"path": "unpre2.md", "content": "x"}), "done"],
            policy=WRITE_NOTES, approver=ScriptedApprover([False]))
        state2 = loop2.run(loop2.new_state())
        self.assertEqual(state2.status, "completed")
        results2 = support.tool_results(support.read_events(self.home))
        self.assertEqual(results2[-1]["error_code"], "denied_by_operator")
        self.assertFalse((self.home / "unpre2.md").exists())


if __name__ == "__main__":
    unittest.main()
