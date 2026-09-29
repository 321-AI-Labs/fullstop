"""Manifest/policy schema+validation matrix, manifest-dir-relative resolution,
and the ERROR_CODES every-code-has-a-producer enumeration."""

import json
import os
import sys
import unittest
from pathlib import Path

import support
from hearth.manifest import (ManifestError, load_manifest,
                             manifest_from_dict)
from hearth.policy import Policy, PolicyError, load_policy, policy_from_dict
from hearth.types import ERROR_CODES, EVENTS, RUN_STATUSES


def base_manifest() -> dict:
    return {
        "identity": {"name": "a", "role": "b", "home": "ws"},
        "goal": "g",
        "policy": {"protected_paths": []},
        "provider": {"type": "scripted", "script_path": "scripts/s.json"},
        "limits": {"max_steps": 5},
    }


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_minimal_manifest_ok(self):
        m = manifest_from_dict(base_manifest(), base_dir=self.base)
        self.assertEqual(m.identity.name, "a")
        self.assertEqual(m.goal, "g")
        self.assertEqual(m.limits.max_steps, 5)
        self.assertEqual(m.limits.max_tool_calls_per_step, 4)
        self.assertEqual(m.log_truncate_chars, 2000)
        self.assertTrue(m.identity.home.is_absolute())

    def test_paths_resolve_relative_to_manifest_dir_from_different_cwd(self):
        # Write the manifest one level deep, run from an unrelated CWD.
        mandir = self.base / "proj" / "deep"
        (mandir / "scripts").mkdir(parents=True)
        (mandir / "scripts" / "s.json").write_text('["ok"]', encoding="utf-8")
        mfile = mandir / "agent.json"
        mfile.write_text(json.dumps(base_manifest()), encoding="utf-8")
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        old_cwd = os.getcwd()
        os.chdir(elsewhere)
        try:
            m = load_manifest(mfile)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(m.provider.script_path, (mandir / "scripts" / "s.json").resolve())
        self.assertEqual(m.identity.home, (mandir / "ws").resolve())
        self.assertIsNone(m.policy_path)          # inline policy form here
        self.assertIsNotNone(m.inline_policy)

    def test_policy_path_form_resolves_relative(self):
        data = base_manifest()
        data["policy"] = "policies/x.json"
        m = manifest_from_dict(data, base_dir=self.base / "proj")
        self.assertEqual(m.policy_path,
                         (self.base / "proj" / "policies" / "x.json").resolve())

    def test_unknown_keys_rejected_anywhere(self):
        for mutate in (
            lambda d: d.update({"bogus": 1}),
            lambda d: d["identity"].update({"bogus": 1}),
            lambda d: d["provider"].update({"bogus": 1}),
            lambda d: d["limits"].update({"bogus": 1}),
        ):
            data = base_manifest()
            mutate(data)
            with self.assertRaises(ManifestError) as ctx:
                manifest_from_dict(data, base_dir=self.base)
            self.assertIn("bogus", str(ctx.exception))

    def test_missing_required_keys(self):
        for key in ("identity", "goal", "policy", "provider", "limits"):
            data = base_manifest()
            del data[key]
            with self.assertRaises(ManifestError) as ctx:
                manifest_from_dict(data, base_dir=self.base)
            self.assertIn(key, str(ctx.exception))

    def test_one_error_lists_every_problem(self):
        data = base_manifest()
        data["bogus_top"] = 1
        data["identity"]["bogus_id"] = 1
        data["limits"]["max_steps"] = 0
        with self.assertRaises(ManifestError) as ctx:
            manifest_from_dict(data, base_dir=self.base)
        msg = str(ctx.exception)
        for bullet in ("bogus_top", "bogus_id", "max_steps"):
            self.assertIn("- ", msg)
            self.assertIn(bullet, msg)
        self.assertEqual(msg.count("\n- "), 3)   # header line + 3 bullets

    def test_max_cost_requires_both_pricing_fields(self):
        data = base_manifest()
        data["limits"]["max_cost_usd"] = 1.0
        with self.assertRaises(ManifestError) as ctx:
            manifest_from_dict(data, base_dir=self.base)
        self.assertIn("max_cost_usd", str(ctx.exception))

    def test_openai_compat_required_fields(self):
        data = base_manifest()
        data["provider"] = {"type": "openai_compat", "base_url": "https://x",
                            "api_key_env": "K", "model": "m",
                            "usd_per_1k_input": 0.5, "usd_per_1k_output": 1.5}
        data["limits"]["max_cost_usd"] = 1.0
        m = manifest_from_dict(data, base_dir=self.base)
        self.assertEqual(m.provider.api_key_env, "K")
        self.assertEqual(m.provider.usd_per_1k_output, 1.5)
        data["provider"].pop("api_key_env")
        with self.assertRaises(ManifestError):
            manifest_from_dict(data, base_dir=self.base)

    def test_unknown_provider_type_rejected(self):
        data = base_manifest()
        data["provider"] = {"type": "anthropic", "x": 1}
        with self.assertRaises(ManifestError):
            manifest_from_dict(data, base_dir=self.base)

    def test_bad_json_is_manifest_error(self):
        bad = self.base / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ManifestError):
            load_manifest(bad)
        with self.assertRaises(ManifestError):
            load_manifest(self.base / "missing.json")


class PolicyTests(unittest.TestCase):
    def test_defaults(self):
        p = policy_from_dict({})
        self.assertEqual(p.protected_paths, ())
        self.assertEqual(p.shell.allow, ())
        self.assertEqual(p.web.max_bytes, 1_048_576)
        self.assertEqual(p.shell.timeout_s, 30.0)

    def test_builtin_protected_non_removable(self):
        p = policy_from_dict({})
        self.assertTrue(p.is_protected(".hearth/activity.jsonl"))
        self.assertTrue(p.is_protected(".HEARTH/STATE.JSON"))

    def test_matching_casefolds_both_sides(self):
        p = policy_from_dict({"protected_paths": ["Secret-*.txt"],
                              "write_preapproved": ["Notes/**"]})
        self.assertTrue(p.is_protected("secret-a.txt"))
        self.assertTrue(p.is_protected("SECRET-A.TXT"))
        self.assertFalse(p.is_protected("plain.txt"))
        self.assertTrue(p.is_write_preapproved("NOTES/deep/x.md"))
        self.assertFalse(p.is_write_preapproved("notes2/x.md"))

    def test_unknown_keys_rejected(self):
        with self.assertRaises(PolicyError):
            policy_from_dict({"nope": 1})
        with self.assertRaises(PolicyError):
            policy_from_dict({"shell": {"nope": 1}})
        with self.assertRaises(PolicyError):
            policy_from_dict({"web": {"nope": 1}})

    def test_load_policy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "p.json"
            f.write_text(json.dumps({"shell": {"allow": ["ls"]}}),
                         encoding="utf-8")
            self.assertEqual(load_policy(f).shell.allow, ("ls",))
            f.write_text("{bad", encoding="utf-8")
            with self.assertRaises(PolicyError):
                load_policy(f)


class ErrorCodeProducerEnumeration(unittest.TestCase):
    """Every member of ERROR_CODES has at least one asserted producer."""

    def test_every_error_code_has_a_producer(self):
        produced = set()
        with support.temp_dir() as td:
            base = Path(td)
            home = support.make_home(base)

            # --- sandbox_escape: glob-pre-approved traversal through the loop
            policy = Policy(write_preapproved=("notes/**",))
            loop = support.build_loop(
                home,
                [support.call_block("file_write",
                                    {"path": "notes/../../escape.txt",
                                     "content": "x"}),
                 "done"],
                policy=policy)
            state = loop.run(loop.new_state())
            codes = {e.get("error_code") for e in
                     support.tool_results(support.read_events(home))}
            self.assertIn("sandbox_escape", codes)
            produced.add("sandbox_escape")

            # --- protected_target: direct write-tool belt (literal protected)
            from hearth.redact import Redactor
            from hearth.tools.file import FileWriteTool
            from hearth.types import ToolCall
            pol = Policy(protected_paths=("secret-*.txt",))
            tool = FileWriteTool(home, Redactor({}), pol)
            result = tool.execute(ToolCall("file_write",
                                           {"path": "secret-a.txt",
                                            "content": "x"}))
            self.assertEqual(result.error_code, "protected_target")
            produced.add("protected_target")

            # --- unknown_tool: registry with a minted ALLOW for a ghost tool
            from hearth.gate import Gate
            from hearth.tools import build_registry
            from hearth.types import Action, GateDecision
            gate = Gate(Policy(), home)
            registry = build_registry(home, Policy(), Redactor({}), gate.verify)
            ghost = ToolCall("ghost-tool", {})
            decision = GateDecision(Action.ALLOW, "test",
                                    token=gate._mint(ghost))
            result = registry.execute(ghost, decision)
            self.assertEqual(result.error_code, "unknown_tool")
            produced.add("unknown_tool")

            # --- not_implemented
            from hearth.tools.browser import BrowserTool
            result = BrowserTool().execute(ToolCall("browser", {}))
            self.assertEqual(result.error_code, "not_implemented")
            produced.add("not_implemented")

            # --- command_not_allowed / shell_disabled / timeout / malformed
            from hearth.policy import ShellPolicy
            from hearth.tools.shell import ShellTool
            denied = ShellTool(ShellPolicy(allow=("ok",), deny=("forbidden",)),
                               home, Redactor({}))
            self.assertEqual(
                denied.execute(ToolCall("shell", {"argv": ["forbidden"]})).error_code,
                "command_not_allowed")
            produced.add("command_not_allowed")
            disabled = ShellTool(ShellPolicy(), home, Redactor({}))
            self.assertEqual(
                disabled.execute(ToolCall("shell", {"argv": ["ls"]})).error_code,
                "shell_disabled")
            produced.add("shell_disabled")
            self.assertEqual(
                denied.execute(ToolCall("shell", {"argv": "ls"})).error_code,
                "malformed_call")
            produced.add("malformed_call")
            slow = ShellTool(ShellPolicy(allow=("a",), timeout_s=0.3),
                             home, Redactor({}))
            self.assertEqual(
                slow.execute(ToolCall("shell", {"argv": [
                    sys.executable, "-c", "import time; time.sleep(2)"]})).error_code,
                "timeout")
            produced.add("timeout")

            # --- web codes via fake fetch
            from hearth.policy import WebPolicy
            from hearth.tools.web import WebFetchTool
            fetch = support.fake_fetch(body=b"x")
            web = WebFetchTool(WebPolicy(deny_domains=("bad.example",)),
                               Redactor({}), fetch=fetch)
            self.assertEqual(web.execute(ToolCall(
                "web_fetch", {"url": "https://bad.example/a"})).error_code,
                "domain_denied")
            produced.add("domain_denied")
            self.assertEqual(web.execute(ToolCall(
                "web_fetch", {"url": "ftp://x/y"})).error_code, "malformed_url")
            produced.add("malformed_url")
            web2 = WebFetchTool(WebPolicy(), Redactor({}),
                                fetch=support.fake_fetch(content_type="image/png",
                                                         body=b"xx"))
            self.assertEqual(web2.execute(ToolCall(
                "web_fetch", {"url": "https://x.example/a"})).error_code,
                "unsupported_media_type")
            produced.add("unsupported_media_type")
            web3 = WebFetchTool(WebPolicy(), Redactor({}),
                                fetch=support.fake_fetch(exc=OSError("boom")))
            self.assertEqual(web3.execute(ToolCall(
                "web_fetch", {"url": "https://x.example/a"})).error_code,
                "io_error")
            produced.add("io_error")

            # --- denied_by_policy / denied_by_operator through the loop
            home2 = support.make_home(base, "home2")
            loop2 = support.build_loop(
                home2,
                [support.call_block("file_write",
                                    {"path": ".hearth/state.json",
                                     "content": "x"}),
                 "done"],
                policy=Policy())
            loop2.run(loop2.new_state())
            codes2 = {e.get("error_code") for e in
                      support.tool_results(support.read_events(home2))}
            self.assertIn("denied_by_policy", codes2)
            produced.add("denied_by_policy")

            home3 = support.make_home(base, "home3")
            from hearth.agent import ScriptedApprover
            loop3 = support.build_loop(
                home3,
                [support.call_block("file_write",
                                    {"path": "x.md", "content": "x"}),
                 "done"],
                policy=Policy(), approver=ScriptedApprover([False]))
            loop3.run(loop3.new_state())
            codes3 = {e.get("error_code") for e in
                      support.tool_results(support.read_events(home3))}
            self.assertIn("denied_by_operator", codes3)
            produced.add("denied_by_operator")

        missing = set(ERROR_CODES) - produced
        self.assertEqual(missing, set(),
                         f"ERROR_CODES members with no producer: {missing}")
        self.assertEqual(produced, set(ERROR_CODES))


class VocabTests(unittest.TestCase):
    def test_vocabularies_frozen(self):
        self.assertEqual(EVENTS[0], "run_start")
        self.assertEqual(EVENTS[-1], "run_end")
        self.assertIn("sandbox_block", EVENTS)
        self.assertEqual(RUN_STATUSES[0], "running")


if __name__ == "__main__":
    unittest.main()
