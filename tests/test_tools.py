"""Per-tool semantics with fakes: alias-scoped read rule, write equality rule
(and its documented fail-closed trailing-dot behavior), list format, shell
disabled/allow/deny/approved-non-allowlisted/env-scrub/timeout, web via fake
fetch, note fixed target + scrub, browser stub."""

import os
import sys
import unittest
from pathlib import Path

import support
from hearth.gate import Gate
from hearth.policy import Policy, ShellPolicy, WebPolicy
from hearth.redact import Redactor
from hearth.tools import build_registry
from hearth.tools.browser import BrowserTool
from hearth.tools.file import FileListTool, FileReadTool, FileWriteTool
from hearth.tools.note import NoteTool
from hearth.tools.shell import ShellTool
from hearth.tools.web import WebFetchTool
from hearth.types import ERROR_CODES, Action, ToolCall

WRITE_ALL = Policy(write_preapproved=("**",))


class RegistryHarness:
    def __init__(self, testcase, policy=None):
        # testcase.tmp is already an absolute Path (NOT a TemporaryDirectory):
        # calling .name on it would yield a bare relative folder name and
        # make_home would create the workspace under CWD (the repo!).
        self.home = support.make_home(testcase.tmp)
        self.policy = policy or WRITE_ALL
        self.gate = Gate(self.policy, self.home)
        self.registry = build_registry(self.home, self.policy,
                                       Redactor({}), self.gate.verify)

    def approved(self, call):
        decision = self.gate.decide(call)
        if decision.action is Action.APPROVAL_REQUIRED:
            decision = self.gate.resolve_approval(call, True)
        return decision

    def execute(self, call):
        return self.registry.execute(call, self.approved(call))


class FileToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.h = RegistryHarness(self)
        self.home = self.h.home

    def test_write_creates_parents_and_overwrites(self):
        result = self.h.execute(ToolCall(
            "file_write", {"path": "notes/deep/a.md", "content": "one"}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("wrote 3 bytes to notes/deep/a.md", result.output)
        result = self.h.execute(ToolCall(
            "file_write", {"path": "notes/deep/a.md", "content": "two"}))
        self.assertTrue(result.ok)
        self.assertEqual((self.home / "notes/deep/a.md").read_text(
            encoding="utf-8"), "two")

    def test_write_scrubs_path_and_content(self):
        marker = "sk-FAKE-tools"
        redactor = Redactor({"HEARTH_KEY": marker})
        gate = Gate(WRITE_ALL, self.home)
        registry = build_registry(self.home, WRITE_ALL, redactor, gate.verify)
        call = ToolCall("file_write", {"path": "leak.md",
                                       "content": f"has {marker}"})
        decision = gate.decide(call)
        self.assertEqual(decision.action, Action.ALLOW)
        result = registry.execute(call, decision)
        self.assertTrue(result.ok, result.error)
        text = (self.home / "leak.md").read_text(encoding="utf-8")
        self.assertNotIn(marker, text)
        self.assertIn("[REDACTED:HEARTH_KEY]", text)

    def test_read_respects_max_bytes_and_bad_utf8(self):
        (self.home / "bin.txt").write_bytes(b"\xff\xfeabc")
        result = self.h.execute(ToolCall(
            "file_read", {"path": "bin.txt", "max_bytes": 2}))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "\ufffd\ufffd")
        full = self.h.execute(ToolCall("file_read", {"path": "bin.txt"}))
        self.assertIn("abc", full.output)

    def test_read_missing_file_is_io_error_via_registry(self):
        result = self.h.execute(ToolCall("file_read", {"path": "nope.txt"}))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "io_error")
        self.assertIn("FileNotFoundError", result.error)

    def test_read_output_scrubbed(self):
        marker = "sk-FAKE-read"
        (self.home / "s.txt").write_text(f"x {marker} y", encoding="utf-8")
        redactor = Redactor({"HEARTH_KEY": marker})
        tool = FileReadTool(self.home, redactor, WRITE_ALL)
        out = tool.execute(ToolCall("file_read", {"path": "s.txt"}))
        self.assertNotIn(marker, out.output)
        self.assertIn("[REDACTED:HEARTH_KEY]", out.output)

    def test_read_malformed_args(self):
        tool = FileReadTool(self.home, Redactor({}), WRITE_ALL)
        for args in ({}, {"path": 5}, {"path": "x", "max_bytes": 0},
                     {"path": "x", "max_bytes": True}):
            result = tool.execute(ToolCall("file_read", args))
            self.assertEqual(result.error_code, "malformed_call", args)

    def test_direct_protected_read_executes_at_tool_level(self):
        # literal-protected spelling: the alias rule does NOT fire (that is
        # what lets operator-approved protected reads execute).
        protected = self.home / ".hearth" / "p.txt"
        protected.write_text("fine", encoding="utf-8")
        tool = FileReadTool(self.home, Redactor({}), Policy())
        result = tool.execute(ToolCall("file_read",
                                       {"path": ".hearth/p.txt"}))
        self.assertTrue(result.ok, result.error)
        self.assertNotEqual(result.error_code, "protected_target")

    def test_write_belt_protected_target_on_direct_misuse(self):
        # Unreachable via the loop (the gate never ALLOWs a protected literal
        # write); documented belt for direct use.
        tool = FileWriteTool(self.home, Redactor({}),
                             Policy(protected_paths=("secret-*.txt",)))
        result = tool.execute(ToolCall("file_write",
                                       {"path": "secret-a.txt",
                                        "content": "x"}))
        self.assertEqual(result.error_code, "protected_target")

    def test_write_equality_rule_blocks_trailing_dot_spelling(self):
        # Verified on this machine: realpath('e.md.') == 'e.md' for an
        # EXISTING file, so resolved != literal -> SandboxError -> fail closed
        # by design (do not "repair": see hearth/tools/file.py docstring).
        (self.home / "x.md").write_text("real", encoding="utf-8")
        result = self.h.execute(ToolCall(
            "file_write", {"path": "x.md.", "content": "sneaky"}))
        self.assertEqual(result.error_code, "sandbox_escape")
        self.assertEqual((self.home / "x.md").read_text(encoding="utf-8"),
                         "real")

    def test_write_equality_rule_blocks_symlinked_file_redirect(self):
        real = self.tmp / "real.md"
        real.write_text("real", encoding="utf-8")
        link = self.home / "sym.md"
        support.try_symlink(self, real, link, "write-redirect")
        result = self.h.execute(ToolCall(
            "file_write", {"path": "sym.md", "content": "sneaky"}))
        self.assertEqual(result.error_code, "sandbox_escape")
        self.assertEqual(real.read_text(encoding="utf-8"), "real")

    def test_file_list_format(self):
        (self.home / "b.txt").write_text("12345", encoding="utf-8")  # 5 bytes
        (self.home / "a.txt").write_text("1", encoding="utf-8")
        (self.home / "sub").mkdir()
        result = self.h.execute(ToolCall("file_list", {"path": "."}))
        self.assertTrue(result.ok)
        lines = result.output.split("\n")
        # name-sorted (casefolded), single level: .hearth, a.txt, b.txt, sub
        self.assertEqual(lines, ["d\t-\t.hearth", "f\t1\ta.txt",
                                 "f\t5\tb.txt", "d\t-\tsub"])
        # single level only
        result = self.h.execute(ToolCall("file_list", {"path": "sub"}))
        self.assertEqual(result.output, "")


class ShellToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.home = support.make_home(Path(self._tmp.name))

    def tool(self, policy=None, scrub_env=()):
        return ShellTool(policy or ShellPolicy(allow=("a",)),
                         self.home, Redactor({}), scrub_env=scrub_env)

    def test_disabled_when_allowlist_empty(self):
        result = self.tool(ShellPolicy()).execute(
            ToolCall("shell", {"argv": ["ls"]}))
        self.assertEqual(result.error_code, "shell_disabled")

    def test_denylist_hard_line(self):
        result = self.tool(ShellPolicy(allow=("ls",), deny=("rm",))).execute(
            ToolCall("shell", {"argv": ["RM", "-rf"]}))
        self.assertEqual(result.error_code, "command_not_allowed")

    def test_malformed_argv(self):
        for argv in ("ls", [], [1], None):
            result = self.tool().execute(ToolCall("shell", {"argv": argv}))
            self.assertEqual(result.error_code, "malformed_call", argv)

    def test_allowlisted_command_runs_in_workspace(self):
        marker = "cwd-marker-xyz"
        (self.home / marker).write_text("", encoding="utf-8")
        tool = self.tool(ShellPolicy(allow=(sys.executable,)))
        result = tool.execute(ToolCall("shell", {"argv": [
            sys.executable, "-c",
            "import os; print(sorted(f for f in os.listdir('.')))"]}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("rc=0", result.output)
        self.assertIn(marker, result.output)

    def test_approved_non_allowlisted_command_executes(self):
        # rev-2 B1: NO allowlist-membership veto at tool level.
        tool = self.tool(ShellPolicy(allow=("something-else",)))
        result = tool.execute(ToolCall("shell", {"argv": [
            sys.executable, "-c", "print('approval-honored')"]}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("approval-honored", result.output)

    def test_env_scrubbed_of_credential_names(self):
        os.environ["HEARTH_TOOL_TEST_KEY"] = "sk-FAKE-shell"
        self.addCleanup(os.environ.pop, "HEARTH_TOOL_TEST_KEY", None)
        tool = self.tool(ShellPolicy(allow=(sys.executable,)),
                         scrub_env=("HEARTH_TOOL_TEST_KEY",))
        result = tool.execute(ToolCall("shell", {"argv": [
            sys.executable, "-c", "import os; print(os.environ)"]}))
        self.assertNotIn("HEARTH_TOOL_TEST_KEY", result.output)
        self.assertNotIn("sk-FAKE-shell", result.output)

    def test_timeout(self):
        tool = self.tool(ShellPolicy(allow=("a",), timeout_s=0.3))
        result = tool.execute(ToolCall("shell", {"argv": [
            sys.executable, "-c", "import time; time.sleep(3)"]}))
        self.assertEqual(result.error_code, "timeout")


class WebToolTests(unittest.TestCase):
    def tool(self, policy=None, **fetch_kwargs):
        return WebFetchTool(policy or WebPolicy(),
                            Redactor({}), fetch=support.fake_fetch(**fetch_kwargs))

    def test_ok_text(self):
        result = self.tool(body=b"hello web").execute(ToolCall(
            "web_fetch", {"url": "https://x.example/a"}))
        self.assertTrue(result.ok, result.error)
        self.assertIn("hello web", result.output)

    def test_allowed_content_types(self):
        for ctype in ("text/html", "application/json", "application/xml",
                      "application/atom+xml", "text/plain; charset=utf-8"):
            result = self.tool(content_type=ctype, body=b"x").execute(
                ToolCall("web_fetch", {"url": "https://x.example/a"}))
            self.assertTrue(result.ok, ctype)

    def test_unsupported_media_type(self):
        for ctype in ("image/png", "application/octet-stream", ""):
            result = self.tool(content_type=ctype).execute(ToolCall(
                "web_fetch", {"url": "https://x.example/a"}))
            self.assertEqual(result.error_code, "unsupported_media_type", ctype)

    def test_size_cap_truncates_but_ok(self):
        policy = WebPolicy(max_bytes=10)
        result = self.tool(policy=policy, body=b"a" * 100).execute(ToolCall(
            "web_fetch", {"url": "https://x.example/a"}))
        self.assertTrue(result.ok)
        self.assertIn("[truncated]", result.output)
        self.assertNotIn("a" * 30, result.output)

    def test_output_capped_at_64k(self):
        result = self.tool(body=b"b" * 200_000).execute(ToolCall(
            "web_fetch", {"url": "https://x.example/a"}))
        self.assertTrue(result.ok)
        # 65536-char cap plus the truncation note
        self.assertLessEqual(len(result.output), 65536 + 40)

    def test_denylist_hard_line(self):
        policy = WebPolicy(deny_domains=("bad.example",))
        calls = []
        result = self.tool(policy=policy, calls=calls).execute(ToolCall(
            "web_fetch", {"url": "https://bad.example/a"}))
        self.assertEqual(result.error_code, "domain_denied")
        self.assertEqual(calls, [])  # never reached fetch

    def test_malformed_urls(self):
        for url in ("ftp://x/", "https:///nohost", "", "not a url"):
            result = self.tool().execute(ToolCall("web_fetch", {"url": url}))
            self.assertEqual(result.error_code, "malformed_url", url)

    def test_fetch_exception_is_io_error(self):
        result = self.tool(exc=OSError("boom")).execute(ToolCall(
            "web_fetch", {"url": "https://x.example/a"}))
        self.assertEqual(result.error_code, "io_error")
        self.assertIn("OSError", result.error)

    def test_non_allowlisted_host_executes_via_fetch(self):
        policy = WebPolicy(allow_domains=("good.example",))
        calls = []
        result = self.tool(policy=policy, body=b"ok", calls=calls).execute(
            ToolCall("web_fetch", {"url": "https://other.example/a"}))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(calls, ["https://other.example/a"])


class NoteToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.home = support.make_home(Path(self._tmp.name))

    def test_appends_scrubbed_timestamped_line_to_fixed_target(self):
        marker = "sk-FAKE-note"
        tool = NoteTool(self.home, Redactor({"HEARTH_KEY": marker}))
        result = tool.execute(ToolCall("note", {"text": f"learn {marker}"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "noted")
        text = (self.home / "memory.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("- ["))
        self.assertIn("learn [REDACTED:HEARTH_KEY]", text)
        self.assertNotIn(marker, text)
        tool.execute(ToolCall("note", {"text": "second"}))
        text2 = (self.home / "memory.md").read_text(encoding="utf-8")
        self.assertEqual(len(text2.strip().split("\n")), 2)

    def test_no_filename_parameter_no_other_files(self):
        tool = NoteTool(self.home, Redactor({}))
        tool.execute(ToolCall("note", {"text": "x", "path": "other.md"}))
        self.assertTrue((self.home / "memory.md").exists())
        self.assertEqual([p.name for p in self.home.iterdir()
                          if p.is_file()], ["memory.md"])

    def test_malformed_text(self):
        tool = NoteTool(self.home, Redactor({}))
        for text in (None, 5, ""):
            result = tool.execute(ToolCall("note", {"text": text}))
            self.assertEqual(result.error_code, "malformed_call")


class BrowserToolTests(unittest.TestCase):
    def test_stub_is_not_implemented(self):
        result = BrowserTool().execute(ToolCall("browser", {"action": "navigate"}))
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "not_implemented")
        self.assertIn("not implemented", result.error)
        schema = BrowserTool().schema()
        self.assertIn("PLANNED", schema["description"])


class ErrorCodeSubsetTests(unittest.TestCase):
    def test_every_code_produced_by_tools_is_in_vocabulary(self):
        # The FULL every-code-has-a-producer enumeration lives in
        # test_manifest_policy.py; this pins the tool-level subset relationship.
        produced = {"sandbox_escape", "protected_target", "unknown_tool",
                    "not_implemented", "command_not_allowed", "shell_disabled",
                    "domain_denied", "malformed_url", "unsupported_media_type",
                    "timeout", "io_error", "malformed_call",
                    "denied_by_policy", "denied_by_operator"}
        self.assertLessEqual(produced, set(ERROR_CODES))
        self.assertEqual(produced, set(ERROR_CODES))


if __name__ == "__main__":
    unittest.main()
