"""Per-tool semantics with fakes: alias-scoped read rule, write equality rule
(and its documented fail-closed trailing-dot behavior), list format, shell
disabled/allow/deny/approved-non-allowlisted/env-scrub/timeout, web via fake
fetch, note fixed target + scrub, browser stub."""

import os
import sys
import unittest
from pathlib import Path

import support
from fullstop.gate import Gate
from fullstop.policy import Policy, ShellPolicy, WebPolicy
from fullstop.redact import Redactor
from fullstop.tools import build_registry
from fullstop.tools.browser import BrowserTool
from fullstop.tools.file import FileListTool, FileReadTool, FileWriteTool
from fullstop.tools.note import NoteTool
from fullstop.tools.shell import ShellTool
from fullstop.tools.web import WebFetchTool
from fullstop.types import ERROR_CODES, Action, ToolCall

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
        redactor = Redactor({"FULLSTOP_KEY": marker})
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
        self.assertIn("[REDACTED:FULLSTOP_KEY]", text)

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
        redactor = Redactor({"FULLSTOP_KEY": marker})
        tool = FileReadTool(self.home, redactor, WRITE_ALL)
        out = tool.execute(ToolCall("file_read", {"path": "s.txt"}))
        self.assertNotIn(marker, out.output)
        self.assertIn("[REDACTED:FULLSTOP_KEY]", out.output)

    def test_read_malformed_args(self):
        tool = FileReadTool(self.home, Redactor({}), WRITE_ALL)
        for args in ({}, {"path": 5}, {"path": "x", "max_bytes": 0},
                     {"path": "x", "max_bytes": True}):
            result = tool.execute(ToolCall("file_read", args))
            self.assertEqual(result.error_code, "malformed_call", args)

    def test_direct_protected_read_executes_at_tool_level(self):
        # literal-protected spelling: the alias rule does NOT fire (that is
        # what lets operator-approved protected reads execute).
        protected = self.home / ".fullstop" / "p.txt"
        protected.write_text("fine", encoding="utf-8")
        tool = FileReadTool(self.home, Redactor({}), Policy())
        result = tool.execute(ToolCall("file_read",
                                       {"path": ".fullstop/p.txt"}))
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
        # v0.2.1: trailing-dot spellings are rejected BEFORE resolution on
        # every platform. On Windows the realpath equality rule alone caught
        # them (realpath('x.md.') == 'x.md' for an existing file); on Linux
        # 'x.md.' is a distinct legal filename, the write SUCCEEDED, and the
        # documented fail-closed promise silently did not hold — the ubuntu
        # CI job caught exactly that. Do not "repair" (file.py docstring).
        (self.home / "x.md").write_text("real", encoding="utf-8")
        result = self.h.execute(ToolCall(
            "file_write", {"path": "x.md.", "content": "sneaky"}))
        self.assertEqual(result.error_code, "sandbox_escape")
        self.assertEqual((self.home / "x.md").read_text(encoding="utf-8"),
                         "real")

    def test_write_equality_rule_blocks_trailing_space_spelling(self):
        # Mirror of the trailing-dot case: trailing spaces are the other
        # Windows-aliasing spelling, rejected pre-resolution everywhere.
        (self.home / "x.md").write_text("real", encoding="utf-8")
        result = self.h.execute(ToolCall(
            "file_write", {"path": "x.md ", "content": "sneaky"}))
        self.assertEqual(result.error_code, "sandbox_escape")
        self.assertEqual((self.home / "x.md").read_text(encoding="utf-8"),
                         "real")

    def test_write_rejects_trailing_dot_or_space_component_mid_path(self):
        # The component check fires on ANY component, not just the leaf —
        # and nothing is created on disk (rejection is pre-resolution).
        for spelling in ("notes./a.md", "notes /a.md"):
            result = self.h.execute(ToolCall(
                "file_write", {"path": spelling, "content": "x"}))
            self.assertEqual(result.error_code, "sandbox_escape", spelling)
        self.assertEqual([p.name for p in self.home.iterdir()],
                         [".fullstop"])

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
        # name-sorted (casefolded), single level: a.txt, b.txt, sub.
        # v0.1.1 (FIXLIST item 13): protected .fullstop/ metadata is HIDDEN
        # from unapproved listings (that leak is what the fix removed); an
        # operator-approved listing of .fullstop itself still shows it.
        self.assertEqual(lines, ["f\t1\ta.txt",
                                 "f\t5\tb.txt", "d\t-\tsub"])
        # single level only
        result = self.h.execute(ToolCall("file_list", {"path": "sub"}))
        self.assertEqual(result.output, "")


class AliasedSandboxRootTests(unittest.TestCase):
    """Tools built over an ALIASED root spelling (symlink standing in for
    GitHub's Windows runners exposing TEMP as the 8.3 short name
    C:\\Users\\RUNNER~1\\...). Rels must be taken against the realpath'd
    root or every write/note dies as a bogus ../.. escape (the 14 windows
    CI failures of 2026-09-30 were exactly this, one root cause)."""

    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = support.make_home(self.tmp / "home")
        self.link = self.tmp / "home-link"
        support.try_symlink(self, self.home, self.link, "sandbox-root-alias")

    def test_write_and_note_land_in_real_home_via_aliased_root(self):
        result = FileWriteTool(self.link, Redactor({}), WRITE_ALL).execute(
            ToolCall("file_write",
                     {"path": "notes/a.md", "content": "via alias"}))
        self.assertTrue(result.ok, result.error)
        self.assertEqual((self.home / "notes" / "a.md").read_text(
            encoding="utf-8"), "via alias")
        note = NoteTool(self.link, Redactor({})).execute(
            ToolCall("note", {"text": "hello"}))
        self.assertTrue(note.ok, note.error)
        self.assertIn("hello", (self.home / "memory.md").read_text(
            encoding="utf-8"))

    def test_alias_read_of_protected_file_via_aliased_root(self):
        protected = self.home / ".fullstop" / "crown.txt"
        protected.write_text("jewels", encoding="utf-8")
        file_alias = self.home / "alias.md"
        support.try_symlink(self, protected, file_alias,
                            "aliased-root-protected-alias")
        result = FileReadTool(self.link, Redactor({}), Policy()).execute(
            ToolCall("file_read", {"path": "alias.md"}))
        self.assertEqual(result.error_code, "protected_target")


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
        os.environ["FULLSTOP_TOOL_TEST_KEY"] = "sk-FAKE-shell"
        self.addCleanup(os.environ.pop, "FULLSTOP_TOOL_TEST_KEY", None)
        tool = self.tool(ShellPolicy(allow=(sys.executable,)),
                         scrub_env=("FULLSTOP_TOOL_TEST_KEY",))
        result = tool.execute(ToolCall("shell", {"argv": [
            sys.executable, "-c", "import os; print(os.environ)"]}))
        self.assertNotIn("FULLSTOP_TOOL_TEST_KEY", result.output)
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
        tool = NoteTool(self.home, Redactor({"FULLSTOP_KEY": marker}))
        result = tool.execute(ToolCall("note", {"text": f"learn {marker}"}))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "noted")
        text = (self.home / "memory.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("- ["))
        self.assertIn("learn [REDACTED:FULLSTOP_KEY]", text)
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
