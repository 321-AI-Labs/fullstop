"""DoD#3: adversarial sandbox matrix against resolve_in_sandbox AND through
live tools, plus the glob-traversal combos and alias rules."""

import os
import unittest
from pathlib import Path

import support
from fullstop.gate import Gate
from fullstop.policy import Policy
from fullstop.redact import Redactor
from fullstop.sandbox import SandboxError, rel_posix, resolve_in_sandbox
from fullstop.tools import build_registry
from fullstop.tools.file import FileReadTool, FileWriteTool
from fullstop.types import Action, ToolCall

ALL_WRITE = Policy(write_preapproved=("**",))  # every literal write ALLOWed at gate


class Harness:
    """Gate + registry over a temp workspace (gate approvals auto-granted)."""

    def __init__(self, testcase, policy=None):
        # testcase.tmp is already an absolute Path — .name here would strip it
        # to a relative folder name created under CWD (the repo).
        self.home = support.make_home(testcase.tmp)
        self.policy = policy or ALL_WRITE
        self.gate = Gate(self.policy, self.home)
        self.registry = build_registry(self.home, self.policy,
                                       Redactor({}), self.gate.verify)

    def approved(self, call: ToolCall):
        decision = self.gate.decide(call)
        if decision.action is Action.APPROVAL_REQUIRED:
            decision = self.gate.resolve_approval(call, True)
        return decision

    def execute(self, call: ToolCall):
        return self.registry.execute(call, self.approved(call))


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.root = support.make_home(Path(self._tmp.name), "ws")

    def test_dot_resolves_to_root(self):
        self.assertEqual(resolve_in_sandbox(self.root, "."),
                         Path(os.path.realpath(self.root)))

    def test_plain_relative_path_resolves(self):
        real = resolve_in_sandbox(self.root, "notes/a.md")
        self.assertEqual(rel_posix(self.root, real), "notes/a.md")

    VECTORS = [
        "../../escape.txt",
        "..\\..\\win.txt",
        "/etc/passwd",
        "C:\\Windows\\x.txt",
        "\\\\server\\share",
        "C:foo",
        "f.txt:evil",
        "file:///x",
        "x\x00y",
        "",
        "notes/sub/../../x",
        "notes/../../escape.txt",
    ]

    def test_vectors_rejected_with_vector_class_message(self):
        for vector in self.VECTORS:
            with self.assertRaises(SandboxError, msg=vector):
                resolve_in_sandbox(self.root, vector)


class ThroughToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.h = Harness(self)
        (self.h.home / "fixture.txt").write_text("fix", encoding="utf-8")

    def outside_files(self):
        return support.snapshot_files(self.tmp) - support.snapshot_files(self.h.home)

    def test_escape_vectors_blocked_through_live_write(self):
        before = self.outside_files()
        for vector in ResolveTests.VECTORS:
            result = self.h.execute(ToolCall(
                "file_write", {"path": vector, "content": "payload"}))
            # "" is malformed at the tool boundary; everything else is the
            # sandbox chokepoint refusing the vector.
            expected = "malformed_call" if vector == "" else "sandbox_escape"
            self.assertEqual(result.error_code, expected,
                             f"{vector}: {result.error}")
            self.assertFalse(result.ok)
        self.assertEqual(self.outside_files(), before)

    def test_read_escape_blocked(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("secret-outside", encoding="utf-8")
        link = self.h.home / "out.md"
        support.try_symlink(self, outside, link, "read-out")
        result = self.h.execute(ToolCall("file_read", {"path": "out.md"}))
        self.assertEqual(result.error_code, "sandbox_escape")

    def test_symlink_alias_into_protected_read(self):
        protected = self.h.home / ".fullstop" / "protected.txt"
        protected.write_text("crown jewels", encoding="utf-8")
        link = self.h.home / "alias.md"
        support.try_symlink(self, protected, link, "alias-protected")
        tool = FileReadTool(self.h.home, Redactor({}), self.h.policy)
        result = tool.execute(ToolCall("file_read", {"path": "alias.md"}))
        self.assertEqual(result.error_code, "protected_target")
        self.assertFalse(result.ok)

    def test_literal_protected_approved_read_executes(self):
        protected = self.h.home / ".fullstop" / "protected.txt"
        protected.write_text("operator let me read this", encoding="utf-8")
        call = ToolCall("file_read", {"path": ".fullstop/protected.txt"})
        decision = self.h.gate.decide(call)
        self.assertEqual(decision.action, Action.APPROVAL_REQUIRED)
        final = self.h.gate.resolve_approval(call, True)
        self.assertEqual(final.action, Action.ALLOW)
        result = self.h.registry.execute(call, final)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.output, "operator let me read this")

    def test_symlinked_dir_write_redirect_blocked(self):
        target_dir = self.tmp / "realnotes"
        target_dir.mkdir()
        link = self.h.home / "noteslink"
        support.try_symlink(self, target_dir, link, "dir-redirect")
        result = self.h.execute(ToolCall(
            "file_write", {"path": "noteslink/x.md", "content": "x"}))
        self.assertEqual(result.error_code, "sandbox_escape")
        self.assertEqual(list(target_dir.iterdir()), [])

    def test_glob_traversal_combos_through_the_loop(self):
        # 'notes/sub/../../x' and 'notes/../../escape.txt' BOTH match 'notes/**'
        # (verified in the plan's grounding): the gate ALLOWs, the sandbox
        # backstop must produce sandbox_escape + sandbox_block, and nothing
        # may appear outside the workspace.
        policy = Policy(write_preapproved=("notes/**",))
        loop = support.build_loop(
            self.h.home,
            [support.call_block("file_write",
                                {"path": "notes/sub/../../x", "content": "a"})
             + "\n" + support.call_block(
                 "file_write",
                 {"path": "notes/../../escape.txt", "content": "b"}),
             "done"],
            policy=policy)
        before = self.outside_files()
        state = loop.run(loop.new_state())
        self.assertEqual(state.status, "completed")
        entries = support.read_events(self.h.home)
        results = support.tool_results(entries)
        self.assertEqual([r["error_code"] for r in results],
                         ["sandbox_escape", "sandbox_escape"])
        blocks = support.events_of(entries, "sandbox_block")
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertIn(block["tool"], ("file_write",))
            self.assertTrue(block["detail"])
        gate_allows = [e for e in support.events_of(entries, "gate_decision")
                       if e["action"] == "allow"]
        self.assertEqual(len(gate_allows), 2)  # the glob DID pre-approve both
        self.assertEqual(self.outside_files(), before)
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertFalse((self.tmp / "x").exists())


class RelPosixTests(unittest.TestCase):
    def test_rel_posix_forward_slashes(self):
        with support.temp_dir() as td:
            root = Path(td)
            self.assertEqual(rel_posix(root, root / "a" / "b.md"), "a/b.md")


if __name__ == "__main__":
    unittest.main()
