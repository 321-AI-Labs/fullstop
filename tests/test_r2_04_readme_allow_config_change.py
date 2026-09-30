"""FIXLIST2 item 4 (low): README missing --allow-config-change.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1.1. The flag
exists (fullstop/cli.py, resume --allow-config-change), its behavior is
pinned by tests (tests/test_fix_02_config_tamper.py: resume refuses a
changed manifest/policy unless the flag is passed; tests/test_agent_loop.py
"the operator raises max_steps and resumes"), but the README never
documents either -- an operator who hits the refusal, or who wants the
standard resume-with-edit flow, finds nothing.

Frozen contract (prose-presence checks, the same style as
tests/test_fix_17_19_readme_honesty.py -- the README is a shipped
artifact; its required content is the contract):

- the README names the flag ``--allow-config-change``;
- the flag is documented in its resume context: what it overrides (resume
  under a CHANGED manifest/policy) and the word resume nearby;
- the resume-with-edit flow is documented with its canonical case: the
  README connects max_steps (raised/increased) with resume and the flag,
  matching the behavior the tests exercise;
- guard (passes today): the underlying refusal behavior -- resume refuses
  changed config -- is already stated and must stay.
"""

import unittest

import support

README = (support.REPO_ROOT / "README.md").read_text(encoding="utf-8")
LOW = README.lower()
FLAG = "--allow-config-change"


def _near(haystack: str, needle: str, word: str, window: int) -> bool:
    """True iff some occurrence of ``needle`` has ``word`` (case-insensitive)
    within ``window`` chars on either side."""
    h = haystack.lower()
    n, w = needle.lower(), word.lower()
    start = 0
    while True:
        i = h.find(n, start)
        if i < 0:
            return False
        if w in h[max(0, i - window):i + len(n) + window]:
            return True
        start = i + 1


class ReadmeAllowConfigChangeTests(unittest.TestCase):
    # -- FAIL today: the flag appears nowhere in the README ---------------

    def test_readme_names_the_flag(self):
        self.assertIn(
            FLAG, README,
            "the README never mentions --allow-config-change, though the "
            "flag ships in the CLI and its behavior is pinned by "
            "tests/test_fix_02_config_tamper.py")

    def test_flag_documented_in_its_resume_context(self):
        self.assertTrue(
            _near(README, FLAG, "resume", 600),
            "the flag must be documented as a resume flag (the word "
            "'resume' near the flag)")
        self.assertTrue(
            _near(README, FLAG, "chang", 600),
            "the flag must be documented as overriding the changed-config "
            "refusal (a 'change(d)' word near the flag)")

    def test_resume_with_edit_flow_documented(self):
        """The flow FIXLIST2 names: raise max_steps, then resume -- which
        changes the manifest bytes, so the flag is part of the same taught
        flow (matching tests/test_agent_loop.py and
        tests/test_fix_02_config_tamper.py)."""
        self.assertTrue(
            _near(README, FLAG, "max_steps", 800),
            "the flag's documentation should teach the canonical "
            "resume-with-edit case: raising max_steps")
        raised = (_near(README, "max_steps", "raise", 400)
                  or _near(README, "max_steps", "increas", 400)
                  or _near(README, "max_steps", "bump", 400))
        self.assertTrue(
            raised,
            "the README must describe raising/increasing max_steps as the "
            "resumable edit (guard-stop -> raise the limit -> resume)")

    # -- guard (passes today; must keep passing) ---------------------------

    def test_readme_states_resume_refuses_changed_config(self):
        """The refusal the flag overrides is already documented
        (claim table: 'resume refuses swapped gate rules'); documenting the
        flag must not replace that statement."""
        self.assertTrue(
            _near(README, "resume", "refus", 300),
            "the README must keep stating that resume refuses a changed "
            "config (the behavior --allow-config-change overrides)")


if __name__ == "__main__":
    unittest.main()
