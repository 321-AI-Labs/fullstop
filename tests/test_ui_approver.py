"""UiApprover: decision-file protocol, fail-closed semantics, forge attempts.

Charter laws pinned here:
- architecture law 2: full render_request output on the card; timeout
  reproduces unattended semantics (stopped_approval via agent.py's existing
  check — test_fix_10 stays green untouched);
- architecture law 3: a forged/planted decision file cannot unlock a
  hard-denied call, and the sandbox refuses writes toward the rendezvous
  directory, so the model cannot self-approve;
- credential law: the pending file is scrubbed before it touches disk.
"""

import json
import shutil
import threading
import time
import unittest
from pathlib import Path

import support
from fullstop.gate import Gate
from fullstop.policy import policy_from_dict
from fullstop.protocol import render_request
from fullstop.redact import Redactor
from fullstop.sandbox import SandboxError, resolve_in_sandbox
from fullstop.types import Action, GateDecision, ToolCall
from fullstop.ui_approver import (UiApprover, decision_path, pending_path,
                                  rendezvous_dir_for, sweep_stale,
                                  write_decision)

LONG = "x" * 9000


def approve_async(approver: UiApprover, call: ToolCall,
                  decision: GateDecision) -> threading.Thread:
    out: dict = {}

    def run():
        out["result"] = approver.approve(call, decision)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.out = out  # type: ignore[attr-defined]
    return t


class UiApproverProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = support.temp_dir()
        self.addCleanup(self.tmp.cleanup)
        self.home = support.make_home(Path(self.tmp.name))
        self.rdir = Path(self.tmp.name) / "rendezvous"
        # Windows: TemporaryDirectory cleanup refuses non-empty dirs, so the
        # rendezvous dir is swept before the tmp dir is removed.
        self.addCleanup(lambda: shutil.rmtree(self.rdir, ignore_errors=True))
        self.call = ToolCall("file_write",
                             {"path": "report.md", "content": "body text"})
        self.decision = GateDecision(Action.APPROVAL_REQUIRED,
                                     "consequential write: report.md")

    def make(self, **kw) -> UiApprover:
        kw.setdefault("rendezvous_dir", self.rdir)
        kw.setdefault("poll_interval_s", 0.02)
        return UiApprover(self.home, **kw)

    def wait_pending(self, approver, timeout_s: float = 5.0) -> Path:
        """Wait until the pending FILE exists on disk: ``last_card`` is set
        just before the atomic write, so the file is the honest signal."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if approver.last_card is not None:
                p = pending_path(self.rdir, approver.last_card["id"])
                if p.exists():
                    return p
            time.sleep(0.01)
        self.fail("approver never wrote a pending card")

    def test_approve_via_decision_file(self):
        approver = self.make()
        t = approve_async(approver, self.call, self.decision)
        p_path = self.wait_pending(approver)
        card = json.loads(p_path.read_text(encoding="utf-8"))
        self.assertEqual(card["tool"], "file_write")
        self.assertEqual(card["reason"], "consequential write: report.md")
        write_decision(self.rdir, card["id"], True)
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertTrue(t.out["result"])
        self.assertFalse(approver.unattended)
        # both files consumed
        self.assertFalse(p_path.exists())
        self.assertFalse(decision_path(self.rdir, card["id"]).exists())

    def test_deny_via_decision_file(self):
        approver = self.make()
        t = approve_async(approver, self.call, self.decision)
        card = json.loads(self.wait_pending(approver).read_text(encoding="utf-8"))
        write_decision(self.rdir, card["id"], False)
        t.join(timeout=5)
        self.assertFalse(t.out["result"])
        self.assertFalse(approver.unattended)  # a human SAID no: attended

    def test_timeout_sets_unattended_and_cleans_up(self):
        approver = self.make(timeout_s=0.2)
        started = time.monotonic()
        self.assertFalse(approver.approve(self.call, self.decision))
        self.assertGreaterEqual(time.monotonic() - started, 0.2)
        self.assertTrue(approver.unattended)
        self.assertEqual(list(self.rdir.glob("pending-*.json")), [])
        self.assertEqual(list(self.rdir.glob("decision-*.json")), [])

    def test_card_carries_complete_render_with_marked_elision(self):
        approver = self.make(timeout_s=0.3)
        call = ToolCall("file_write", {"path": "big.md", "content": LONG})
        t = approve_async(approver, call, self.decision)
        card = json.loads(self.wait_pending(approver).read_text(encoding="utf-8"))
        # The render is EXACTLY protocol.render_request output: the product's
        # own display law (4000/arg, 8000 total) with clearly marked elision,
        # never a silent summary.
        self.assertEqual(card["rendered"], render_request(call))
        self.assertIn("...[truncated ", card["rendered"])
        t.join(timeout=5)  # times out; cleans its own files up

    def test_wrong_id_and_malformed_decision_files_are_ignored(self):
        # A pre-planted decision under a different id must never satisfy a
        # fresh prompt (the id is 128-bit secrets randomness per prompt).
        forged = "deadbeef" * 2
        write_decision(self.rdir, forged, True)
        approver = self.make(timeout_s=0.2)
        self.assertFalse(approver.approve(self.call, self.decision))
        self.assertTrue(approver.unattended)
        # the forged file was not consumed by the timeout cleanup (wrong id)
        self.assertTrue(decision_path(self.rdir, forged).exists())
        sweep_stale(self.rdir, now=time.time() + 25 * 3600)
        self.assertFalse(decision_path(self.rdir, forged).exists())

    def test_unwritable_channel_fails_closed(self):
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        approver = UiApprover(self.home, timeout_s=60, poll_interval_s=0.02,
                              rendezvous_dir=blocker)
        started = time.monotonic()
        self.assertFalse(approver.approve(self.call, self.decision))
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(approver.unattended)

    def test_pending_scrubbed_before_disk(self):
        secret = "sk-super-secret-value-123"
        approver = self.make(redactor=Redactor({"MY_KEY": secret}),
                             timeout_s=0.3)
        call = ToolCall("file_write",
                        {"path": "creds.md", "content": f"key={secret}"})
        t = approve_async(approver, call, self.decision)
        p_path = self.wait_pending(approver)
        raw = p_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertIn("[REDACTED:MY_KEY]", raw)
        t.join(timeout=5)
        self.assertTrue(approver.unattended)  # timed out; files cleaned

    def test_pending_cards_read_side(self):
        approver = self.make()
        t = approve_async(approver, self.call, self.decision)
        p_path = self.wait_pending(approver)
        card_id = approver.last_card["id"]
        cards = approver.pending_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], card_id)
        self.assertFalse(cards[0].get("expired"))
        # answered cards disappear from the read side
        write_decision(self.rdir, card_id, True)
        self.assertEqual(approver.pending_cards(), [])
        t.join(timeout=5)
        # stale-by-timeout cards are marked expired, still visible (honest)
        approver2 = self.make(timeout_s=0.05)
        t2 = approve_async(approver2, self.call, self.decision)
        self.wait_pending(approver2)
        t2.join(timeout=5)
        # approver2 cleaned up on timeout; plant one manually to test expiry
        stale = dict(approver2.last_card)
        write_decision(self.rdir, "0" * 16, True)  # unrelated, cleaned below
        pending = pending_path(self.rdir, stale["id"])
        pending.write_text(json.dumps(stale), encoding="utf-8")
        cards = approver.pending_cards(now=time.time() + 3600)
        self.assertTrue(cards and cards[0].get("expired"))
        pending.unlink()
        decision_path(self.rdir, "0" * 16).unlink()


class UnattendedSemanticsTests(unittest.TestCase):
    """The loop must land in stopped_approval on UiApprover timeout — the
    same semantics test_fix_10 pins for the non-tty console approver."""

    def test_timeout_stops_in_stopped_approval(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            rdir = Path(tmp) / "r"
            policy = policy_from_dict({"write_preapproved": ["notes/**"]})
            approver = UiApprover(home, timeout_s=0.15, poll_interval_s=0.02,
                                  rendezvous_dir=rdir)
            replies = [support.call_block(
                "file_write", {"path": "report.md", "content": "x"})]
            loop = support.build_loop(home, replies, policy=policy,
                                      approver=approver)
            state = loop.run(loop.new_state())
            self.assertEqual(state.status, "stopped_approval")
            events = support.read_events(home)
            trips = support.events_of(events, "guard_trip")
            self.assertEqual(trips[-1]["kind"], "approval_unattended")

    def test_operate_the_decision_file_completes_the_run(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            rdir = Path(tmp) / "r"
            policy = policy_from_dict({"write_preapproved": ["notes/**"]})
            approver = UiApprover(home, timeout_s=30, poll_interval_s=0.02,
                                  rendezvous_dir=rdir)
            replies = [
                support.call_block("file_write",
                                   {"path": "report.md", "content": "body"}),
                "done",
            ]
            loop = support.build_loop(home, replies, policy=policy,
                                      approver=approver)

            def answer_when_asked():
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if approver.last_card is not None:
                        write_decision(rdir, approver.last_card["id"], True)
                        return
                    time.sleep(0.01)

            answerer = threading.Thread(target=answer_when_asked, daemon=True)
            answerer.start()
            state = loop.run(loop.new_state())
            answerer.join(timeout=5)
            self.assertEqual(state.status, "completed")
            self.assertTrue((home / "report.md").exists())
            self.assertFalse(approver.unattended)


class HardDenyForgeTests(unittest.TestCase):
    """Charter architecture law 3: an approval decision file CANNOT unlock a
    hard-denied call. Two layers pinned: the gate never consults an approver
    for a DENY (and resolve_approval never upgrades it), and the sandbox
    refuses writes toward the rendezvous dir, so the model cannot forge its
    own decision file in the first place."""

    POLICY = policy_from_dict({"protected_paths": ["secret-*.txt"]})

    def test_hard_deny_never_reaches_the_approver(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            rdir = Path(tmp) / "r"
            # Pre-plant a forged approval decision in the rendezvous dir.
            write_decision(rdir, "cafebabe" * 2, True)
            approver = UiApprover(home, timeout_s=30, poll_interval_s=0.02,
                                  rendezvous_dir=rdir)
            replies = [
                support.call_block("file_write",
                                   {"path": "secret-keys.txt",
                                    "content": "must never land"}),
                "done",
            ]
            loop = support.build_loop(home, replies, policy=self.POLICY,
                                      approver=approver)
            state = loop.run(loop.new_state())
            self.assertEqual(state.status, "completed")
            events = support.read_events(home)
            results = support.tool_results(events)
            self.assertEqual(results[0]["error_code"], "denied_by_policy")
            self.assertFalse((home / "secret-keys.txt").exists())
            # No approval was ever requested; no card was ever minted.
            self.assertEqual(support.events_of(events, "approval_request"), [])
            self.assertIsNone(approver.last_card)
            # The forged file is still sitting there, unconsumed.
            self.assertTrue(decision_path(rdir, "cafebabe" * 2).exists())

    def test_resolve_approval_cannot_upgrade_a_hard_deny(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            gate = Gate(self.POLICY, home)
            call = ToolCall("file_write",
                            {"path": "secret-keys.txt", "content": "x"})
            decision = gate.decide(call)
            self.assertIs(decision.action, Action.DENY)
            forced = gate.resolve_approval(call, True)  # operator says yes
            self.assertIs(forced.action, Action.DENY)

    def test_sandbox_refuses_writes_to_the_rendezvous_dir(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            rdir = rendezvous_dir_for(home)
            target = str(rdir / "decision-deadbeefdeadbeef.json")
            with self.assertRaises(SandboxError):
                resolve_in_sandbox(home, target)          # absolute
            rel = "../../../" + "tmpdir-climb/"
            with self.assertRaises(SandboxError):
                resolve_in_sandbox(home, rel)             # parent traversal
            with self.assertRaises(SandboxError):
                resolve_in_sandbox(home, "C:\\evil\\decision.json")

    def test_model_cannot_reach_rendezvous_via_file_write(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            rdir = rendezvous_dir_for(home)
            policy = policy_from_dict({"write_preapproved": ["**"]})
            approver = UiApprover(home, timeout_s=30, poll_interval_s=0.02,
                                  rendezvous_dir=rdir)
            # Even fully pre-approved writes cannot escape the sandbox toward
            # the decision-file channel.
            replies = [
                support.call_block(
                    "file_write",
                    {"path": "../../" + rdir.name + "/decision-x.json",
                     "content": '{"decision": "approve"}'}),
                "done",
            ]
            loop = support.build_loop(home, replies, policy=policy,
                                      approver=approver)
            state = loop.run(loop.new_state())
            events = support.read_events(home)
            results = support.tool_results(events)
            self.assertEqual(results[0]["error_code"], "sandbox_escape")
            self.assertEqual(list(rdir.glob("decision-*.json")), [])
            self.assertEqual(state.status, "completed")


if __name__ == "__main__":
    unittest.main()
