"""Chain integrity: verify() intact and after char-flip/line-delete/reorder/
hash-edit; resume chains across close/reopen; append onto a corrupt last line
raises; scrub+truncate at write time; reserved fields rejected."""

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (sys.path bootstrap for `import fullstop`)

from fullstop.activity import ActivityLog, ActivityLogError
from fullstop.redact import Redactor


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "log" / "activity.jsonl"

    def _log(self, redactor=None, truncate_chars=2000):
        return ActivityLog(self.path, redactor=redactor,
                           truncate_chars=truncate_chars)

    def _lines(self):
        return [ln for ln in self.path.read_text(encoding="utf-8").split("\n")
                if ln.strip()]

    def test_append_and_verify_intact(self):
        log = self._log()
        for i in range(5):
            log.append("step", n=i, note=f"entry {i}")
        self.assertEqual(log.verify(), (True, None))
        entries = list(log.entries())
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3, 4, 5])
        self.assertEqual(entries[0]["prev"], "0")
        self.assertEqual(entries[1]["prev"], entries[0]["hash"])
        self.assertEqual(entries[-1]["event"], "step")
        self.assertEqual(len(log.tail(2)), 2)

    def test_chain_continues_across_close_and_reopen(self):
        log = self._log()
        first_hash = log.append("run_start", goal="g")
        log.append("step", n=1)
        log2 = self._log()
        log2.append("step", n=2)
        entries = list(log2.entries())
        self.assertEqual(entries[2]["prev"], entries[1]["hash"])
        self.assertEqual(entries[0]["hash"], first_hash)
        self.assertEqual(log2.verify(), (True, None))

    def _corrupt(self, transform):
        lines = self._lines()
        lines = transform(lines)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_char_flip_detected(self):
        log = self._log()
        log.append("run_start", goal="abc")
        log.append("step", n=1)
        self._corrupt(lambda ls: [ls[0].replace("abc", "abd"), ls[1]])
        ok, bad = self._log().verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_line_delete_detected(self):
        log = self._log()
        for i in range(4):
            log.append("step", n=i)
        self._corrupt(lambda ls: ls[:2] + ls[3:])
        ok, bad = self._log().verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 3)

    def test_reorder_detected(self):
        log = self._log()
        for i in range(3):
            log.append("step", n=i)
        self._corrupt(lambda ls: [ls[1], ls[0], ls[2]])
        ok, bad = self._log().verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_hash_edit_detected(self):
        log = self._log()
        log.append("step", n=1)
        obj = json.loads(self._lines()[0])
        obj["hash"] = ("0" * 64)
        self.path.write_text(json.dumps(obj, sort_keys=True,
                                        separators=(",", ":")) + "\n",
                             encoding="utf-8")
        ok, bad = self._log().verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    def test_append_onto_corrupt_tail_raises(self):
        log = self._log()
        log.append("step", n=1)
        self._corrupt(lambda ls: [ls[0].replace('"n":1', '"n":9')])
        with self.assertRaises(ActivityLogError):
            self._log().append("step", n=2)
        # and nothing was written
        self.assertEqual(len(self._lines()), 1)

    def test_append_onto_unparseable_tail_raises(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("garbage not json\n", encoding="utf-8")
        with self.assertRaises(ActivityLogError):
            self._log().append("step")

    def test_scrub_and_truncate_at_write_time(self):
        marker = "sk-FAKE-value-123"
        redactor = Redactor({"FULLSTOP_KEY": marker})
        log = self._log(redactor=redactor, truncate_chars=50)
        log.append("model_reply", content=f"leak {marker} here")
        log.append("model_reply", content="y" * 500)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(marker, text)
        self.assertIn("[REDACTED:FULLSTOP_KEY]", text)
        entries = list(log.entries())
        self.assertLessEqual(len(entries[1]["content"]), 50 + 40)  # + truncation note
        self.assertIn("truncated", entries[1]["content"])

    def test_reserved_field_names_rejected(self):
        log = self._log()
        for name in ("seq", "ts", "prev", "hash"):
            with self.assertRaises(ValueError):
                log.append("step", **{name: 1})
        # "event" collides with append()'s own parameter at the Python level
        with self.assertRaises(TypeError):
            log.append("step", **{"event": 1})

    def test_scrub_reaches_nested_values(self):
        marker = "sk-FAKE-nested"
        log = self._log(redactor=Redactor({"K": marker}))
        log.append("gate_decision", args={"content": f"has {marker}"})
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(marker, text)
        self.assertIn("[REDACTED:K]", text)


if __name__ == "__main__":
    unittest.main()
