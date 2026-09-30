"""The front-end assets: the inline-script breakout guard on render_html
and the naming law (no prose in the assets; every S.* key the script reads
exists in ui_strings.STRINGS)."""

import json
import re
import unittest

from fullstop import ui_assets, ui_strings


class ScriptBreakoutTests(unittest.TestCase):
    def test_render_refuses_a_script_close_tag_in_the_blob(self):
        # window.STRINGS is injected verbatim inside an inline <script>;
        # a "</script" in the JSON would break out of the element.
        for bad in ("</script>", "</SCRIPT>", "x</ScRiPt y"):
            with self.subTest(bad=bad):
                with self.assertRaises(AssertionError):
                    ui_assets.render_html(json.dumps({"k": bad}))

    def test_shipped_strings_blob_is_breakout_clean(self):
        blob = json.dumps(ui_strings.STRINGS)
        self.assertNotIn("</script", blob.lower())
        html = ui_assets.render_html(blob)
        self.assertIn("window.STRINGS = ", html)


class NamingLawTests(unittest.TestCase):
    def test_every_js_string_key_exists_in_the_blob(self):
        keys = set(re.findall(r"\bS\.([A-Za-z_][A-Za-z0-9_]*)",
                              ui_assets.APP_JS))
        self.assertEqual(keys - set(ui_strings.STRINGS), set())

    def test_html_shell_carries_no_prose(self):
        html = ui_assets.APP_HTML
        self.assertIn("<title></title>", html)  # boot sets document.title
        self.assertNotIn('aria-label="', html)  # boot sets it from STRINGS
        self.assertNotIn(">...</div>", html)    # boot fills loading states

    def test_js_carries_none_of_the_swept_prose_literals(self):
        for literal in ("'args'", "'output'", "'OK '", "'ERROR '",
                        "'manifest'", "'steps_done'", "'HTTP '",
                        "'reply \\u00b7 tokens '"):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, ui_assets.APP_JS)


if __name__ == "__main__":
    unittest.main()
