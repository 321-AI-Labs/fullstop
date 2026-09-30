"""Live-check bug 2 (2026-09-30): fences glued to prose parse as no-call.

Observed live against glm-5.3-flash: a reply of the exact shape

    I'll start by overwriting poem.txt with the required content.<<<TOOL_CALL>>>
    {"name": "file_write", "args": {"path": "poem.txt", "content": "OVERRIDE WORKS"}}
    <<<END_TOOL_CALL>>>

completed the run with status=completed and steps_done=0: the line-anchored
fence match (stripped == marker) missed a fence glued directly onto prose,
so a well-formed call was silently dropped and the loop treated the reply as
a no-call completion. For an always-on agent, silent false-completion is a
trust bug. Frozen contract: fences are recognized anywhere in the reply.
"""

import unittest

from fullstop.protocol import parse_tool_calls

GLUED = (
    "I'll start by overwriting poem.txt with the required content."
    "<<<TOOL_CALL>>>\n"
    '{"name": "file_write", "args": {"path": "poem.txt", "content": "OVERRIDE WORKS"}}\n'
    "<<<END_TOOL_CALL>>>"
)

GLUED_CLOSE = (
    "prose\n<<<TOOL_CALL>>>\n"
    '{"name": "note", "args": {"text": "sea"}}'
    "<<<END_TOOL_CALL>>> done now."
)


class FenceParsingTests(unittest.TestCase):
    def test_open_fence_glued_to_prose_is_parsed(self):
        calls = parse_tool_calls(GLUED)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0].call)
        self.assertEqual(calls[0].call.name, "file_write")
        self.assertEqual(calls[0].call.args["content"], "OVERRIDE WORKS")

    def test_close_fence_glued_to_prose_is_parsed(self):
        calls = parse_tool_calls(GLUED_CLOSE)
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(calls[0].call)
        self.assertEqual(calls[0].call.name, "note")

    def test_unterminated_glued_fence_still_flagged(self):
        calls = parse_tool_calls("prose<<<TOOL_CALL>>>\n" '{"name": "note"}')
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0].call)
        self.assertIn("unterminated", calls[0].error)

    def test_no_markers_still_empty(self):
        self.assertEqual(parse_tool_calls("just prose, no fences"), [])


if __name__ == "__main__":
    unittest.main()
