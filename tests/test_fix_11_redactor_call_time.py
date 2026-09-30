"""FIXLIST item 11 (minor): Redactor snapshots env at start, not call time.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The defect
(turn-02 critique 8): ``Redactor.from_env`` reads the env values once at
process start while the provider reads the key at CALL time — a mid-run
credential rotation leaves the NEW value unredacted, straining the charter
law "values are read at call time".

Frozen contract: the redactor reads key material at CALL time — a value
that appears (or changes) in the environment after construction must be
scrubbed (the old value stops being scrubbed once rotated away).
"""

import os
import unittest

import support
from fullstop.redact import Redactor

NAME = "FULLSTOP_FIX11_KEY"
OLD = "sk-fix11-old-" + "o" * 12
NEW = "sk-fix11-new-" + "n" * 12


class RedactorCallTimeTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop(NAME, None)
        self.addCleanup(os.environ.pop, NAME, None)

    def test_value_set_after_construction_is_scrubbed(self):
        redactor = Redactor.from_env([NAME])
        os.environ[NAME] = NEW
        self.assertNotIn(NEW, redactor.scrub(f"leak: {NEW}"),
                         "a credential that appeared after Redactor "
                         "construction went unredacted")

    def test_rotated_value_is_scrubbed_at_call_time(self):
        os.environ[NAME] = OLD
        redactor = Redactor.from_env([NAME])
        self.assertNotIn(OLD, redactor.scrub(f"a {OLD} b"))  # sanity
        os.environ[NAME] = NEW  # rotation mid-run
        scrubbed = redactor.scrub(f"a {OLD} b {NEW} c")
        self.assertNotIn(NEW, scrubbed,
                         "the rotated-in value must be read at call time")
        self.assertIn("[REDACTED:" + NAME + "]", scrubbed)


if __name__ == "__main__":
    unittest.main()
