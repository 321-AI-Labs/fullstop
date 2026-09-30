"""Loads the shipped example manifests FROM DISK via the real loaders
(proving manifest-dir-relative resolution). Each scripted example is RUN
keylessly with its home redirected to a temp dir via dataclasses.replace:
scripted-demo with ScriptedApprover([True]) pinned to its exactly-one prompt,
research-readonly with zero prompts, writer-approval with exactly two —
each asserting its EXACT artifact set. The real-provider template,
research-assistant.json + its policy, is validated by load only (exactly
what the README claims)."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

import support
from fullstop.activity import ActivityLog
from fullstop.agent import AgentLoop, ScriptedApprover
from fullstop.manifest import load_manifest
from fullstop.policy import load_policy
from fullstop.provider import ScriptedModel
from fullstop.redact import Redactor
from fullstop.state import activity_path

EXAMPLES = support.REPO_ROOT / "examples"


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_scripted_demo_from_disk_exactly_one_prompt(self):
        manifest = load_manifest(EXAMPLES / "scripted-demo.json")
        policy = load_policy(manifest.policy_path)
        self.assertEqual(policy.write_preapproved, ("notes/**", "memory.md"))
        # redirect ONLY home; policy + script stay pointed at the shipped files
        home = support.make_home(self.tmp, "workspace-demo")
        manifest = replace(manifest,
                           identity=replace(manifest.identity, home=home))
        provider = ScriptedModel.from_json_file(manifest.provider.script_path)
        approver = ScriptedApprover([True])
        log = ActivityLog(activity_path(home))
        loop = AgentLoop(manifest, policy, provider, log, Redactor({}),
                         approver=approver)
        state = loop.run(loop.new_state())

        # exactly one prompt: the summary.md write
        self.assertEqual(len(approver.prompts), 1,
                         f"prompts: {approver.prompts}")
        self.assertIn("summary.md", approver.prompts[0])
        self.assertEqual(state.status, "completed")

        # EXACT artifact set inside the redirected home
        self.assertTrue((home / "memory.md").exists())
        self.assertIn("Starting the demo run.",
                      (home / "memory.md").read_text(encoding="utf-8"))
        self.assertTrue((home / "notes" / "intro.md").exists())
        self.assertEqual((home / "notes" / "intro.md").read_text(
            encoding="utf-8"), "# Intro: fullstop demo workspace.")
        self.assertTrue((home / "summary.md").exists())  # approved write ran

        # no file outside the temp home (the glob-disguised escape was blocked)
        outside = [str(p) for p in self.tmp.rglob("*")
                   if p.is_file() and home not in p.parents]
        self.assertEqual(outside, [], "escape or stray file outside home")

        entries = support.read_events(home)
        self.assertEqual([e["event"] for e in entries][0], "run_start")
        blocks = support.events_of(entries, "sandbox_block")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["tool"], "file_write")
        results = support.tool_results(entries)
        self.assertEqual(results[-1]["error_code"], "sandbox_escape")

        self.assertEqual(ActivityLog(activity_path(home)).verify(),
                         (True, None))

    def test_research_readonly_from_disk_zero_prompts(self):
        """The read-only researcher: listings are ALLOW, both write attempts
        (file_write, note) are hard-DENIED as protected paths, no human is
        prompted, the workspace is untouched, and the chain verifies."""
        manifest = load_manifest(EXAMPLES / "research-readonly.json")
        policy = load_policy(manifest.policy_path)
        self.assertEqual(policy.write_preapproved, ())
        self.assertIn("notes/**", policy.protected_paths)
        self.assertIn("memory.md", policy.protected_paths)
        # redirect ONLY home; policy + script stay pointed at the shipped files
        home = support.make_home(self.tmp, "workspace-research-readonly")
        manifest = replace(manifest,
                           identity=replace(manifest.identity, home=home))
        provider = ScriptedModel.from_json_file(manifest.provider.script_path)
        approver = ScriptedApprover([])  # any prompt here would fail the test
        log = ActivityLog(activity_path(home))
        loop = AgentLoop(manifest, policy, provider, log, Redactor({}),
                         approver=approver)
        state = loop.run(loop.new_state())

        self.assertEqual(state.status, "completed")
        self.assertEqual(approver.prompts, [],
                         "the read-only run must never prompt")
        results = support.tool_results(support.read_events(home))
        self.assertEqual(
            [(r["tool"], r["ok"], r["error_code"]) for r in results],
            [("file_list", True, None),
             ("file_write", False, "denied_by_policy"),
             ("note", False, "denied_by_policy"),
             ("file_list", True, None)])
        # the workspace holds exactly .fullstop/ — nothing was written
        self.assertEqual(sorted(p.name for p in home.iterdir()), [".fullstop"])
        self.assertEqual(ActivityLog(activity_path(home)).verify(),
                         (True, None))

    def test_writer_approval_from_disk_exactly_two_prompts(self):
        """The approval-gated writer: one promptless pre-approved write
        (drafts/**), one approved write (report.md), one operator-denied
        note, one hard-denied protected write (secret-*.txt — approval can
        never upgrade it); exactly two prompts; the chain verifies."""
        manifest = load_manifest(EXAMPLES / "writer-approval.json")
        policy = load_policy(manifest.policy_path)
        self.assertEqual(policy.write_preapproved, ("drafts/**",))
        self.assertIn("secret-*.txt", policy.protected_paths)
        home = support.make_home(self.tmp, "workspace-writer-approval")
        manifest = replace(manifest,
                           identity=replace(manifest.identity, home=home))
        provider = ScriptedModel.from_json_file(manifest.provider.script_path)
        approver = ScriptedApprover([True, False])
        log = ActivityLog(activity_path(home))
        loop = AgentLoop(manifest, policy, provider, log, Redactor({}),
                         approver=approver)
        state = loop.run(loop.new_state())

        self.assertEqual(state.status, "completed")
        self.assertEqual(len(approver.prompts), 2)
        self.assertIn("report.md", approver.prompts[0])
        # note's target is fixed (types.NOTE_FILENAME = memory.md), so the
        # faithful render shows the call, not the target path
        self.assertTrue(approver.prompts[1].startswith("note("),
                        f"second prompt should be the note: {approver.prompts[1]!r}")
        self.assertTrue((home / "drafts" / "outline.md").exists())
        self.assertTrue((home / "report.md").exists())        # approved write ran
        self.assertFalse((home / "memory.md").exists())       # note denied by operator
        self.assertFalse((home / "secret-keys.txt").exists())  # protected: unapprovable
        results = support.tool_results(support.read_events(home))
        self.assertEqual(
            [(r["tool"], r["ok"], r["error_code"]) for r in results],
            [("file_write", True, None),
             ("file_write", True, None),
             ("note", False, "denied_by_operator"),
             ("file_write", False, "denied_by_policy")])
        self.assertEqual(ActivityLog(activity_path(home)).verify(),
                         (True, None))

    def test_research_assistant_validates_by_load_only(self):
        manifest = load_manifest(EXAMPLES / "research-assistant.json")
        self.assertEqual(manifest.provider.type, "openai_compat")
        self.assertEqual(manifest.provider.api_key_env, "FULLSTOP_API_KEY")
        self.assertIn("REPLACE-WITH-YOUR-MODEL-ID", manifest.provider.model)
        self.assertEqual(manifest.credential_env_vars, ("FULLSTOP_API_KEY",))
        self.assertIsNotNone(manifest.limits.max_cost_usd)
        self.assertIsNotNone(manifest.provider.usd_per_1k_input)
        self.assertIsNotNone(manifest.provider.usd_per_1k_output)
        policy = load_policy(manifest.policy_path)
        # v0.1.1 (FIXLIST item 7): the example ships CONSTRAINED entries —
        # program + argument patterns; bare argv[0]-only entries are the
        # defect the fix removed from the docs.
        self.assertEqual(policy.shell.allow, (
            {"program": "git", "args": ["status"]},
            {"program": "git", "args": ["log", "--oneline", "*"]},
        ))
        self.assertEqual(policy.shell.deny, ("rm",))
        self.assertEqual(policy.web.allow_domains,
                         ("en.wikipedia.org", "arxiv.org", "pypi.org"))

    def test_demo_script_file_is_valid_json_list(self):
        replies = json.loads((EXAMPLES / "scripts" / "demo-script.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(len(replies), 5)
        self.assertTrue(all(isinstance(r, str) for r in replies))


if __name__ == "__main__":
    unittest.main()
