"""The wizard: round-trip through the REAL validators, verbatim field-level
errors, single-file (inline policy) output, new-file-only atomic saves,
credential law (names only; values can never be persisted)."""

import json
import os
import unittest
from pathlib import Path

import support
from fullstop.manifest import load_manifest
from fullstop.policy import policy_from_dict
from fullstop.ui_wizard import (_map_errors, build_manifest_dict,
                                save_manifest, validate_form)


def good_form(tmp: Path) -> dict:
    home = tmp / "agent-home"
    mandir = tmp / "manifests"
    mandir.mkdir(parents=True, exist_ok=True)
    script = mandir / "script.json"
    script.write_text('["done"]', encoding="utf-8")
    return {
        "name": "writer", "role": "drafts reports", "home": str(home),
        "goal": "Draft the weekly report.",
        "providerType": "scripted", "scriptPath": str(script),
        "maxSteps": "6", "maxCalls": "4", "logTruncate": "2000",
        "credentials": "REPORT_API_KEY, OTHER_NAME",
        "protected": ".env\nsecret-*.txt",
        "preapproved": "drafts/**\nmemory.md",
        "shellAllow": '{"program": "git", "args": ["status"]}\npython',
        "shellDeny": "rm",
        "webAllow": "example.com",
        "webDeny": "evil.example",
        "savePath": str(mandir / "writer.json"),
    }


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = support.temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.form = good_form(Path(self.tmp.name))

    def test_valid_form_round_trips_through_real_validators(self):
        verdict = validate_form(self.form)
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(verdict["field_errors"], {})
        self.assertEqual(verdict["global_errors"], [])

    def test_written_file_loads_with_the_real_loader(self):
        verdict = save_manifest(self.form)
        self.assertTrue(verdict["ok"], verdict)
        path = Path(verdict["saved_path"])
        self.assertTrue(path.exists())
        manifest = load_manifest(path)  # the strict loader, incl. tamper guard
        self.assertEqual(manifest.identity.name, "writer")
        self.assertIsNotNone(manifest.inline_policy)
        policy = policy_from_dict(manifest.inline_policy or {})
        self.assertIn("drafts/**", policy.write_preapproved)
        self.assertEqual(policy.shell.allow,
                         ({"program": "git", "args": ["status"]}, "python"))
        self.assertEqual(policy.web.allow_domains, ("example.com",))
        # ONE file written; no policy file anywhere
        self.assertEqual(sorted(p.name for p in path.parent.glob("*.json")),
                         ["script.json", "writer.json"])

    def test_verbatim_field_level_errors(self):
        form = dict(self.form, name="", maxSteps="abc", maxCost="5")
        verdict = validate_form(form)
        self.assertFalse(verdict["ok"])
        self.assertIn("identity.name must be a non-empty string",
                      verdict["field_errors"]["name"])
        self.assertIn("limits.max_steps must be a positive integer",
                      verdict["field_errors"]["maxSteps"])
        # cross-field rule lands verbatim on the max-cost field
        self.assertTrue(any(
            "requires BOTH provider.usd_per_1k_input" in m
            for m in verdict["field_errors"]["maxCost"]),
            verdict["field_errors"])

    def test_provider_errors_map_to_the_right_fields(self):
        form = dict(self.form, providerType="openai_compat", scriptPath="")
        verdict = validate_form(form)
        self.assertFalse(verdict["ok"])
        self.assertIn("openai_compat provider requires base_url",
                      verdict["field_errors"]["baseUrl"])
        self.assertIn("openai_compat provider requires api_key_env",
                      verdict["field_errors"]["apiKeyEnv"])
        self.assertIn("openai_compat provider requires model",
                      verdict["field_errors"]["model"])

    def test_policy_errors_map_to_the_right_fields(self):
        # A JSON allow entry missing "args" parses fine as JSON but is
        # rejected by the POLICY validator — verbatim, on the shell field.
        form = dict(self.form, shellAllow='{"program": "git"}')
        verdict = validate_form(form)
        self.assertFalse(verdict["ok"])
        self.assertEqual(len(verdict["field_errors"]["shellAllow"]), 1)
        self.assertIn("exactly the keys", verdict["field_errors"]["shellAllow"][0])
        self.assertIn("shell.allow entries must be",
                      verdict["field_errors"]["shellAllow"][0])

    def test_map_errors_keeps_unmappable_lines_verbatim(self):
        message = ("invalid manifest:\n"
                   "- identity.name must be a non-empty string\n"
                   "- unknown manifest key: mischief\n"
                   "- limits.max_cost_usd requires BOTH provider.usd_per_1k_input and "
                   "provider.usd_per_1k_output (cost cannot be tracked without pricing)")
        field_errors, unmapped = _map_errors(message)
        self.assertEqual(field_errors["name"],
                         ["identity.name must be a non-empty string"])
        self.assertEqual(field_errors["maxCost"],
                         ["limits.max_cost_usd requires BOTH "
                          "provider.usd_per_1k_input and "
                          "provider.usd_per_1k_output (cost cannot be tracked "
                          "without pricing)"])
        self.assertEqual(unmapped, ["unknown manifest key: mischief"])


class SaveLawTests(unittest.TestCase):
    def setUp(self):
        self.tmp = support.temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.form = good_form(Path(self.tmp.name))

    def test_refuses_existing_path(self):
        path = Path(self.form["savePath"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        verdict = save_manifest(self.form)
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["field_errors"]["savePath"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_refuses_save_inside_home(self):
        home = Path(self.tmp.name) / "agent-home"
        home.mkdir(parents=True, exist_ok=True)
        form = dict(self.form, savePath=str(home / "manifest.json"))
        verdict = save_manifest(form)
        self.assertFalse(verdict["ok"])
        self.assertTrue(verdict["field_errors"]["savePath"])
        self.assertFalse((home / "manifest.json").exists())

    def test_invalid_form_writes_nothing(self):
        form = dict(self.form, name="")
        verdict = save_manifest(form)
        self.assertFalse(verdict["ok"])
        self.assertFalse(Path(self.form["savePath"]).exists())

    def test_atomic_write_no_tmp_left_behind(self):
        verdict = save_manifest(self.form)
        self.assertTrue(verdict["ok"])
        folder = Path(verdict["saved_path"]).parent
        self.assertEqual(list(folder.glob("*.tmp")), [])

    def test_env_values_never_in_written_bytes(self):
        secret = "sk-wizard-test-secret-3f9a"
        os.environ["FULLSTOP_WIZ_TEST_KEY"] = secret
        self.addCleanup(os.environ.pop, "FULLSTOP_WIZ_TEST_KEY", None)
        form = dict(self.form, credentials="FULLSTOP_WIZ_TEST_KEY",
                    # a value PASTED into a text field still cannot persist
                    goal=f"Use {secret} carefully.")
        verdict = save_manifest(form)
        self.assertTrue(verdict["ok"], verdict)
        raw = Path(verdict["saved_path"]).read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertIn("FULLSTOP_WIZ_TEST_KEY", raw)  # the NAME is there
        self.assertIn("[REDACTED:FULLSTOP_WIZ_TEST_KEY]", raw)

    def test_empty_credentials_write_no_credentials_key(self):
        verdict = save_manifest(dict(self.form, credentials=""))
        self.assertTrue(verdict["ok"], verdict)
        data = json.loads(Path(verdict["saved_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("credentials", data)


class BuildDictTests(unittest.TestCase):
    def test_numbers_pass_raw_garbage_through(self):
        d = build_manifest_dict({"name": "n", "role": "r", "home": "h",
                                 "goal": "g", "providerType": "scripted",
                                 "maxSteps": "abc"})
        self.assertEqual(d["limits"]["max_steps"], "abc")  # validator's call


if __name__ == "__main__":
    unittest.main()
