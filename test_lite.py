"""Unit tests for lite. Run: python3 -m unittest test_lite -v"""

import contextlib
import io
import json
import unittest
from unittest import mock

import lite


def _tool_call(command):
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {
                    "name": "sh",
                    "arguments": json.dumps({"command": command}),
                },
            }
        ],
    }


def _calls_of(reply):
    calls = []
    for tc in reply.get("tool_calls") or []:
        fn = tc["function"]
        calls.append((fn["name"], json.loads(fn["arguments"])))
    return reply.get("content") or "", calls


class ShTests(unittest.TestCase):
    def test_output_and_exit_code(self):
        out = lite.sh("echo hello")
        self.assertIn("hello", out)
        self.assertIn("exit code: 0", out)

    def test_nonzero_exit(self):
        self.assertIn("exit code: 3", lite.sh("exit 3"))

    def test_output_is_truncated(self):
        with mock.patch.object(lite, "TOOL_OUTPUT_LIMIT", 5):
            out = lite.sh("echo hello")
        self.assertIn("[truncated", out)
        self.assertNotIn("hello", out)


class RunTaskTests(unittest.TestCase):
    def test_runs_tool_then_finishes(self):
        replies = iter([_tool_call("echo hi"), {"content": "done"}])
        seen = []

        def fake_call_llm(messages):
            seen.append(messages[:])
            return _calls_of(next(replies))

        with mock.patch.object(lite, "call_llm", fake_call_llm):
            result = lite.run_task("run a command")
        self.assertEqual(result, "done")
        tool_msgs = [m for m in seen[1] if m["role"] == "tool"]
        self.assertTrue(any("hi" in m["content"] for m in tool_msgs))

    def test_final_message_without_tools(self):
        with mock.patch.object(lite, "call_llm", return_value=("all done", [])):
            self.assertEqual(lite.run_task("finish"), "all done")

    def test_step_limit(self):
        def fake_call_llm(messages):
            return "", [("sh", {"command": "echo x"})]

        with mock.patch.object(lite, "call_llm", fake_call_llm):
            self.assertEqual(lite.run_task("loop", steps=2), "hit step limit")

    def test_cap_messages(self):
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        messages += [{"role": "assistant", "content": f"a{i}"} for i in range(40)]
        capped = lite.cap_messages(messages)
        self.assertLessEqual(len(capped), lite.MESSAGE_CAP)
        self.assertEqual(capped[0]["content"], "s")
        self.assertEqual(capped[1]["content"], "u")
        self.assertEqual(capped[-1]["content"], "a39")


class MainTests(unittest.TestCase):
    def test_missing_api_key(self):
        with mock.patch.object(lite, "API_KEY", None):
            with contextlib.redirect_stderr(io.StringIO()):
                code = lite.main(["some task"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
