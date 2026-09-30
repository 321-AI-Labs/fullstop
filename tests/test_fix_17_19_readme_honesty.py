"""FIXLIST items 17-19 (README, charter + honesty law): the honest
comparison table, the missing non-goals, and the context-exhaustion limit
line.

Freeze-tests-before-fixes: EXPECTED TO FAIL against v0.1. The charter's
honesty law: every README claim must be demonstrable; anything untested is
listed under "not yet". These tests pin the REQUIRED README content as
prose-presence checks (the README is itself a shipped artifact; its
required sections are the contract):

- item 17: a comparison vs Dots' ANNOUNCED capabilities covering all three
  categories — has / has not / will not — scoped to what was announced
  (no press-speculation comparisons, no mark misuse such as
  "Dots-compatible");
- item 18: the non-goals list includes the plugin marketplace and
  non-OpenAI-compatible provider protocols (Anthropic native etc.);
- item 19: the context-exhaustion limit line for FIXLIST item 3 (the
  `stopped_context` behavior must be stated as a limit, not hidden).
"""

import re
import unittest
from pathlib import Path

import support

README = (support.REPO_ROOT / "README.md").read_text(encoding="utf-8")
LOW = README.lower()


class ReadmeHonestyTests(unittest.TestCase):
    def test_readme_exists_and_mentions_dots(self):
        """Context assert: the comparison counterpart is named (passes
        today; keeps the file loadable for the rest)."""
        self.assertIn("Dots", README)

    # -- item 17 -------------------------------------------------------------

    def test_comparison_covers_has_hasnot_willnot(self):
        for pattern in (r"\bhas not\b", r"\bwill not\b"):
            with self.subTest(pattern=pattern):
                self.assertRegex(
                    README, pattern,
                    "the honest comparison table must cover all three "
                    "categories: has / has not / will not")

    def test_comparison_is_scoped_to_announced_capabilities(self):
        self.assertRegex(
            README, r"\bannounced\b",
            "the comparison must be scoped to Dots' ANNOUNCED capabilities "
            "(dated/sourced), not press speculation")

    def test_no_dots_compatible_phrasing(self):
        """GUARD (passes today): no OpenAI mark misuse such as
        'Dots-compatible'."""
        self.assertNotIn("Dots-compatible", README)
        self.assertNotIn("dots-compatible", LOW)

    # -- item 18 -------------------------------------------------------------

    def test_non_goals_include_plugin_marketplace(self):
        self.assertTrue(
            "plugin marketplace" in LOW,
            "the README non-goals list must include the plugin marketplace "
            "(charter non-goal added by FIXLIST item 18)")

    def test_non_goals_include_non_openai_compatible_protocols(self):
        self.assertTrue(
            "anthropic" in LOW,
            "the README non-goals list must name non-OpenAI-compatible "
            "provider protocols (Anthropic native etc.)")

    # -- item 19 -------------------------------------------------------------

    def test_context_exhaustion_limit_line(self):
        stated = ("stopped_context" in LOW) or (
            "context" in LOW and "exhaustion" in LOW)
        self.assertTrue(
            stated,
            "the README must state the context-exhaustion limit plainly "
            "(unbounded message growth -> stopped_context; FIXLIST item 3)")


if __name__ == "__main__":
    unittest.main()
