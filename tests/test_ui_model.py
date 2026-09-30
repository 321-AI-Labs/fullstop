"""The UI read-model: snapshot, incremental log reading (rotation-aware),
history grouping, verify wrapper. Numbers from real data only (plan law)."""

import json
import unittest
from pathlib import Path

import support
from fullstop.activity import ActivityLog
from fullstop.policy import policy_from_dict
from fullstop.ui_model import (LogReader, history, load_snapshot,
                               snapshot_view, verify)


def priced_manifest(home: Path, script_path):
    from fullstop.manifest import Identity, Limits, Manifest, ProviderConfig
    return Manifest(
        identity=Identity(name="t", role="t", home=Path(home)),
        goal="priced goal",
        policy_path=None,
        inline_policy={"write_preapproved": ["notes/**", "memory.md"]},
        provider=ProviderConfig(type="scripted", script_path=Path(script_path),
                                usd_per_1k_input=2.0, usd_per_1k_output=4.0),
        limits=Limits(max_steps=8),
    )


class SnapshotTests(unittest.TestCase):
    def test_snapshot_none_without_checkpoint(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            self.assertIsNone(load_snapshot(home))

    def test_cost_equals_checkpoint_cost_from_priced_run(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            replies = [support.call_block("note", {"text": "hi"}), "done"]
            manifest = priced_manifest(
                home, support.write_script(home / ".fullstop", replies))
            loop = support.build_loop(home, replies, manifest=manifest)
            state = loop.run(loop.new_state())
            self.assertGreater(state.cost_usd, 0)
            snap = load_snapshot(home)
            self.assertEqual(snap["cost_usd"], state.cost_usd)
            view = snapshot_view(snap)
            self.assertEqual(view["cost_usd"], state.cost_usd)
            self.assertEqual(view["steps_done"], state.steps_done)
            self.assertTrue(view["live"] is False)


class LogReaderTests(unittest.TestCase):
    def run_demo(self, home) -> None:
        policy = {"write_preapproved": ["notes/**"],
                  "protected_paths": ["secret-*.txt"]}
        replies = [
            support.call_block("note", {"text": "start"}),
            support.call_block("file_write",
                               {"path": "notes/a.md", "content": "A"}),
            "done",
        ]
        loop = support.build_loop(home, replies, policy=policy_from_dict(policy))
        loop.run(loop.new_state())

    def test_reader_yields_every_event_type_written(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            self.run_demo(home)
            entries = LogReader(home).new_entries()
            events = {e["event"] for e in entries}
            for expected in ("run_start", "model_reply", "tool_call",
                             "gate_decision", "tool_result", "step",
                             "checkpoint", "run_end", "config_loaded"):
                self.assertIn(expected, events)
            # gate decisions carry their reason + classification (the chips)
            gates = [e for e in entries if e["event"] == "gate_decision"]
            self.assertTrue(all("reason" in e for e in gates))
            self.assertTrue(all("classification" in e for e in gates))
            # lossless: entries are exactly the file lines
            raw = [json.loads(ln) for ln in
                   (home / ".fullstop" / "activity.jsonl")
                   .read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(entries, raw)

    def test_incremental_no_duplicates(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = ActivityLog(home / ".fullstop" / "activity.jsonl")
            log.append("run_start", run_id="r1")
            log.append("step", n=1)
            reader = LogReader(home)
            first = reader.new_entries()
            self.assertEqual(len(first), 2)
            self.assertEqual(reader.last_seq, 2)
            log.append("step", n=2)
            log.append("run_end", status="completed")
            second = reader.new_entries()
            self.assertEqual([e["event"] for e in second],
                             ["step", "run_end"])
            self.assertEqual(reader.last_seq, 4)
            self.assertEqual(reader.new_entries(), [])

    def test_rotation_across_segments(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = ActivityLog(home / ".fullstop" / "activity.jsonl",
                              max_segment_bytes=1024)
            reader = LogReader(home)
            seen: list[int] = []
            for i in range(60):
                log.append("step", n=i, pad="p" * 40)
                seen.extend(e["seq"] for e in reader.new_entries())
            # segments actually rotated
            rotated = list((home / ".fullstop").glob("activity-*.jsonl"))
            self.assertTrue(rotated)
            # every seq exactly once, in order, despite rotation
            self.assertEqual(seen, list(range(1, 61)))

    def test_torn_tail_line_waits_for_newline(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log_path = home / ".fullstop" / "activity.jsonl"
            log = ActivityLog(log_path)
            log.append("run_start", run_id="r1")
            # simulate a concurrent half-written append
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write('{"seq": 2, "event": "ste')
            reader = LogReader(home)
            self.assertEqual(len(reader.new_entries()), 1)
            # complete the line (with a trailing newline): now it is visible
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write('p"}\n')
            got = reader.new_entries()
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0]["seq"], 2)


class HistoryTests(unittest.TestCase):
    def _log(self, home):
        return ActivityLog(home / ".fullstop" / "activity.jsonl")

    def test_two_runs_grouped_with_statuses(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = self._log(home)
            log.append("run_start", run_id="r1", goal="first")
            log.append("model_reply", content="x", input_tokens=10,
                       output_tokens=5)
            log.append("step", n=1)
            log.append("run_end", status="completed")
            log.append("run_start", run_id="r2", goal="second")
            log.append("model_reply", content="y", input_tokens=7,
                       output_tokens=3)
            log.append("step", n=1)
            runs = history([json.loads(ln) for ln in
                            (home / ".fullstop" / "activity.jsonl")
                            .read_text(encoding="utf-8").splitlines()
                            if ln.strip()])
            self.assertEqual([r["run_id"] for r in runs], ["r1", "r2"])
            self.assertEqual(runs[0]["status"], "completed")
            self.assertEqual(runs[0]["tokens_in"], 10)
            self.assertEqual(runs[1]["status"], "running")  # newest, no end

    def test_crashed_older_run_is_interrupted_not_running(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = self._log(home)
            log.append("run_start", run_id="r1", goal="crashed")
            log.append("step", n=1)
            # no run_end for r1: a later run starts
            log.append("run_start", run_id="r2", goal="ok")
            log.append("run_end", status="completed")
            runs = history([json.loads(ln) for ln in
                            (home / ".fullstop" / "activity.jsonl")
                            .read_text(encoding="utf-8").splitlines()
                            if ln.strip()])
            self.assertEqual(runs[0]["status"], "interrupted")
            self.assertEqual(runs[1]["status"], "completed")


class VerifyTests(unittest.TestCase):
    def _log(self, home):
        return ActivityLog(home / ".fullstop" / "activity.jsonl")

    def test_verify_intact_and_tampered(self):
        with support.temp_dir() as tmp:
            home = support.make_home(Path(tmp))
            log = self._log(home)
            log.append("run_start", run_id="r1")
            log.append("step", n=1)
            self.assertEqual(verify(home), (True, None))
            lines = (home / ".fullstop" / "activity.jsonl") \
                .read_text(encoding="utf-8").splitlines()
            victim = json.loads(lines[1])
            victim["n"] = 999
            lines[1] = json.dumps(victim, sort_keys=True,
                                  separators=(",", ":"))
            (home / ".fullstop" / "activity.jsonl").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
            ok, first_bad = verify(home)
            self.assertFalse(ok)
            self.assertEqual(first_bad, 2)

    def test_verify_never_creates_the_log_dir(self):
        with support.temp_dir() as tmp:
            bare = Path(tmp) / "empty-home"
            bare.mkdir()
            self.assertEqual(verify(bare), (True, None))
            self.assertEqual(list(bare.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
