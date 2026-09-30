"""Parser adversarial matrix + observation determinism."""

import unittest

import support
from fullstop.protocol import (TOOL_CALL_CLOSE, TOOL_CALL_OPEN,
                             format_observation, parse_tool_calls, summary)
from fullstop.types import ToolCall, ToolResult


def block(body: str) -> str:
    return f"{TOOL_CALL_OPEN}\n{body}\n{TOOL_CALL_CLOSE}"


class ParserTests(unittest.TestCase):
    def test_no_markers_returns_empty(self):
        self.assertEqual(parse_tool_calls("hello world"), [])
        self.assertEqual(parse_tool_calls(""), [])

    def test_spoofed_case_is_not_a_marker(self):
        text = "<<<tool_call>>>\n{}\n<<<end_tool_call>>>"
        self.assertEqual(parse_tool_calls(text), [])

    def test_single_valid_block(self):
        out = parse_tool_calls(block('{"name": "file_read", "args": {"path": "x"}}'))
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].error)
        self.assertEqual(out[0].call.name, "file_read")
        self.assertEqual(out[0].call.args, {"path": "x"})

    def test_flat_scalars_accepted(self):
        body = ('{"name": "t", "args": {"a": "s", "b": 1, "c": 1.5, '
                '"d": true, "e": false, "f": null}}')
        out = parse_tool_calls(block(body))
        self.assertIsNone(out[0].error)

    def test_nested_value_rejected_but_flat_list_allowed(self):
        # flat lists of scalars are legal (shell argv); nesting is not
        ok = parse_tool_calls(block(
            '{"name": "shell", "args": {"argv": ["ls", "-la"]}}'))
        self.assertIsNone(ok[0].error)
        for bad in ('{"name": "t", "args": {"a": {"b": 1}}}',
                    '{"name": "t", "args": {"a": [[1], 2]}}',
                    '{"name": "t", "args": {"a": [{"b": 1}]}}'):
            out = parse_tool_calls(block(bad))
            self.assertIsNotNone(out[0].error, bad)
            self.assertIsNone(out[0].call)

    def test_extra_keys_rejected_including_approved(self):
        body = '{"name": "t", "args": {}, "approved": true}'
        out = parse_tool_calls(block(body))
        self.assertIsNotNone(out[0].error)
        self.assertIn('exactly', out[0].error)
        body2 = '{"tool": "t", "args": {}}'
        self.assertIsNotNone(parse_tool_calls(block(body2))[0].error)

    def test_wrong_types_rejected(self):
        for body in ('{"name": 5, "args": {}}',
                     '{"name": "", "args": {}}',
                     '{"name": "t", "args": []}',
                     '"just a string"',
                     '[]'):
            out = parse_tool_calls(block(body))
            self.assertIsNotNone(out[0].error, body)

    def test_invalid_json_rejected(self):
        out = parse_tool_calls(block("{not json"))
        self.assertIsNotNone(out[0].error)

    def test_multiple_blocks_parsed_in_order(self):
        text = (block('{"name": "a", "args": {}}')
                + "\n"
                + block('{"name": "b", "args": {}}'))
        out = parse_tool_calls(text)
        self.assertEqual([p.call.name for p in out], ["a", "b"])

    def test_oversized_block_rejected(self):
        body = '{"name": "t", "args": {"k": "' + "x" * 262200 + '"}}'
        out = parse_tool_calls(block(body))
        self.assertIsNotNone(out[0].error)
        self.assertIn("large", out[0].error)

    def test_count_cap(self):
        text = "\n".join(block(f'{{"name": "t{i}", "args": {{}}}}') for i in range(10))
        out = parse_tool_calls(text, max_calls=8)
        self.assertEqual(len(out), 9)
        valid = [p for p in out if p.call is not None]
        self.assertEqual(len(valid), 8)
        self.assertEqual(out[-1].error, "too many tool call blocks")

    def test_unterminated_block(self):
        out = parse_tool_calls(f"{TOOL_CALL_OPEN}\n" + '{"name": "t", "args": {}}')
        self.assertEqual(len(out), 1)
        self.assertIsNotNone(out[0].error)
        self.assertIn("unterminated", out[0].error)

    def test_nested_marker_inside_body_is_not_valid_json(self):
        text = (f"{TOOL_CALL_OPEN}\n"
                f'{{"name": "t", "args": {{}}}}\n{TOOL_CALL_OPEN}\n'
                f'{{"name": "u", "args": {{}}}}\n{TOOL_CALL_CLOSE}')
        out = parse_tool_calls(text)
        self.assertIsNotNone(out[0].error)  # body contains a raw marker line


class ObservationTests(unittest.TestCase):
    def test_format_observation_deterministic(self):
        call = ToolCall("file_read", {"path": "x"})
        result = ToolResult(ok=True, output="hello")
        a = format_observation(call, result)
        b = format_observation(call, result)
        self.assertEqual(a, b)
        self.assertIn("OK", a)
        self.assertIn("file_read", a)

    def test_format_observation_error_and_truncation(self):
        call = ToolCall("shell", {"argv": ["ls"]})
        result = ToolResult(ok=False, output="", error="denied",
                            error_code="denied_by_policy")
        text = format_observation(call, result)
        self.assertIn("ERROR denied_by_policy: denied", text)
        long = ToolResult(ok=True, output="x" * 5000)
        truncated = format_observation(call, long, truncate_chars=100)
        self.assertIn("x" * 100, truncated)
        self.assertIn("...[truncated 4900 chars]", truncated)
        self.assertLess(len(truncated), 250)

    def test_summary_compact_and_sorted(self):
        call = ToolCall("file_write", {"path": "a.md", "content": "x" * 100})
        s = summary(call)
        self.assertTrue(s.startswith("file_write("))
        # keys are sorted for determinism: content < path
        self.assertLess(s.index("content="), s.index("path="))
        self.assertIn("...", s)  # long value elided


if __name__ == "__main__":
    unittest.main()
