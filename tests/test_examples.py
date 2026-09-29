"""Loads the shipped example trio FROM DISK via the real loaders (proving
manifest-dir-relative resolution), redirects only identity.home to a temp dir
via dataclasses.replace, runs keylessly with ScriptedApprover([True]) pinned
to the demo's exactly-one prompt, and asserts the EXACT artifact set.
research-assistant.json + its policy are validated by load only (exactly what
the README claims)."""

import json
import unittest
from dataclasses import replace
from pathlib import Path

import support
from hearth.activity import ActivityLog
from hearth.agent import AgentLoop, ScriptedApprover
from hearth.manifest import load_manifest
from hearth.policy import load_policy
from hearth.provider import ScriptedModel
from hearth.redact import Redactor
from hearth.state import activity_path

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
            encoding="utf-8"), "# Intro: hearth demo workspace.")
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

    def test_research_assistant_validates_by_load_only(self):
        manifest = load_manifest(EXAMPLES / "research-assistant.json")
        self.assertEqual(manifest.provider.type, "openai_compat")
        self.assertEqual(manifest.provider.api_key_env, "HEARTH_API_KEY")
        self.assertIn("REPLACE-WITH-YOUR-MODEL-ID", manifest.provider.model)
        self.assertEqual(manifest.credential_env_vars, ("HEARTH_API_KEY",))
        self.assertIsNotNone(manifest.limits.max_cost_usd)
        self.assertIsNotNone(manifest.provider.usd_per_1k_input)
        self.assertIsNotNone(manifest.provider.usd_per_1k_output)
        policy = load_policy(manifest.policy_path)
        self.assertEqual(policy.shell.allow, ("ls", "git"))
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
