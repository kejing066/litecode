"""Unit tests for lite. Run: python3 -m unittest test_lite -v"""

import contextlib
import io
import json
import os
import tempfile
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


class LiteTestCase(unittest.TestCase):
    """Deterministic base: cleared environment, temp config dirs."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.global_dir = os.path.join(self.tmp, "global")
        self.opencode_dir = os.path.join(self.tmp, "opencode-global")
        self.project_dir = os.path.join(self.tmp, "proj")
        for d in (self.global_dir, self.opencode_dir, self.project_dir):
            os.makedirs(d)
        clearer = mock.patch.dict(os.environ, {}, clear=True)
        clearer.start()
        self.addCleanup(clearer.stop)
        os.environ["LITECODE_CONFIG_DIR"] = self.global_dir
        os.environ["LITECODE_OPENCODE_CONFIG_DIR"] = self.opencode_dir


class ShTests(LiteTestCase):
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


class ConfigTests(LiteTestCase):
    def load(self):
        return lite.load_config(project_dir=self.project_dir)

    def test_load_config_project_file_with_jsonc(self):
        path = os.path.join(self.project_dir, "lite.json")
        with open(path, "w") as fh:
            fh.write(
                "{\n"
                "  // provider config, OpenCode-style\n"
                '  "model": "openrouter/openai/gpt-4o",\n'
                '  "providers": {\n'
                '    "openrouter": {\n'
                '      "name": "OpenRouter",\n'
                '      "settings": {\n'
                '        "baseURL": "https://openrouter.ai/api/v1",\n'
                '        "apiKey": "{env:TEST_KEY}",\n'
                "      },\n"  # trailing comma
                "    },\n"
                "  },\n"
                "}\n"
            )
        with mock.patch.dict(os.environ, {"TEST_KEY": "abc"}):
            cfg = self.load()
        self.assertEqual(cfg["model"], "openrouter/openai/gpt-4o")
        self.assertEqual(
            cfg["providers"]["openrouter"]["settings"]["apiKey"], "abc"
        )

    def test_load_config_global_overridden_by_project(self):
        with open(os.path.join(self.global_dir, "opencode.json"), "w") as fh:
            json.dump({"model": "openai/gpt-4o"}, fh)
        with open(os.path.join(self.project_dir, "lite.json"), "w") as fh:
            json.dump({"model": "openai/gpt-4o-mini"}, fh)
        self.assertEqual(self.load()["model"], "openai/gpt-4o-mini")

    def test_load_config_skips_malformed_file(self):
        with open(os.path.join(self.project_dir, "lite.json"), "w") as fh:
            fh.write("{ not json ")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.load(), {})

    def test_resolve_config_defaults(self):
        cfg = lite.resolve_config({})
        self.assertEqual(cfg["provider"], "openai")
        self.assertEqual(cfg["model"], "gpt-4o-mini")
        self.assertEqual(cfg["base_url"], "https://api.openai.com/v1")

    def test_resolve_config_splits_model_ref(self):
        cfg = lite.resolve_config({}, model="openai/gpt-4o")
        self.assertEqual(cfg["provider"], "openai")
        self.assertEqual(cfg["model"], "gpt-4o")

    def test_resolve_config_object_model(self):
        cfg = lite.resolve_config(
            {
                "providers": {
                    "openrouter": {
                        "name": "OpenRouter",
                        "settings": {"baseURL": "https://openrouter.ai/api/v1"},
                    }
                }
            },
            model={"providerID": "openrouter", "model": "anthropic/claude-3"},
        )
        self.assertEqual(cfg["provider"], "openrouter")
        self.assertEqual(cfg["provider_name"], "OpenRouter")
        self.assertEqual(cfg["model"], "anthropic/claude-3")
        self.assertEqual(cfg["base_url"], "https://openrouter.ai/api/v1")

    def test_resolve_config_model_env_override(self):
        with mock.patch.dict(os.environ, {"LITECODE_MODEL": "gpt-4o"}):
            cfg = lite.resolve_config({})
        self.assertEqual(cfg["model"], "gpt-4o")
        self.assertEqual(cfg["provider"], "openai")

    def test_resolve_config_api_key_from_provider_env(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
            cfg = lite.resolve_config({})
        self.assertEqual(cfg["api_key"], "env-key")

    def test_resolve_config_api_key_from_config(self):
        cfg = lite.resolve_config(
            {"providers": {"openai": {"settings": {"apiKey": "cfg-key"}}}}
        )
        self.assertEqual(cfg["api_key"], "cfg-key")

    def test_resolve_config_api_key_from_env_list(self):
        with mock.patch.dict(os.environ, {"ACME_KEY": "list-key"}):
            cfg = lite.resolve_config(
                {
                    "providers": {
                        "acme": {
                            "env": ["ACME_KEY"],
                            "settings": {"baseURL": "https://acme.example/v1"},
                        }
                    }
                },
                model="acme/coder",
            )
        self.assertEqual(cfg["provider"], "acme")
        self.assertEqual(cfg["api_key"], "list-key")
        self.assertEqual(cfg["base_url"], "https://acme.example/v1")

    def test_resolve_config_api_base_env(self):
        with mock.patch.dict(
            os.environ, {"LITECODE_API_BASE": "https://proxy.example/v1"}
        ):
            cfg = lite.resolve_config({})
        self.assertEqual(cfg["base_url"], "https://proxy.example/v1")

    def test_call_llm_requires_api_key(self):
        cfg = {
            "provider": "openai",
            "base_url": "https://x",
            "model": "m",
            "api_key": None,
        }
        with self.assertRaises(RuntimeError) as ctx:
            lite.call_llm([], cfg)
        self.assertIn("API key", str(ctx.exception))

    def test_call_llm_requires_base_url(self):
        cfg = {"provider": "acme", "base_url": None, "model": "m", "api_key": "k"}
        with self.assertRaises(RuntimeError) as ctx:
            lite.call_llm([], cfg)
        self.assertIn("base URL", str(ctx.exception))


class RunTaskTests(LiteTestCase):
    def test_runs_tool_then_finishes(self):
        replies = iter([_tool_call("echo hi"), {"content": "done"}])
        seen = []
        seen_configs = []

        def fake_call_llm(messages, config=None):
            seen.append(messages[:])
            seen_configs.append(config)
            return _calls_of(next(replies))

        with mock.patch.object(lite, "call_llm", fake_call_llm):
            result = lite.run_task("run a command")
        self.assertEqual(result, "done")
        self.assertEqual(seen_configs[0]["model"], lite.DEFAULT_MODEL)
        tool_msgs = [m for m in seen[1] if m["role"] == "tool"]
        self.assertTrue(any("hi" in m["content"] for m in tool_msgs))

    def test_final_message_without_tools(self):
        with mock.patch.object(lite, "call_llm", return_value=("all done", [])):
            self.assertEqual(lite.run_task("finish"), "all done")

    def test_step_limit(self):
        def fake_call_llm(messages, config=None):
            return "", [("sh", {"command": "echo x"})]

        with mock.patch.object(lite, "call_llm", fake_call_llm):
            self.assertEqual(lite.run_task("loop", steps=2), "hit step limit")

    def test_on_event_events(self):
        events = []

        def fake_call_llm(messages, config=None):
            return "", [("sh", {"command": "echo hi"})]

        def on_event(role, text):
            events.append((role, text))

        with mock.patch.object(lite, "call_llm", fake_call_llm):
            result = lite.run_task("task", steps=1, on_event=on_event)
        self.assertEqual(result, "hit step limit")
        self.assertIn(("status", "step 1"), events)
        self.assertTrue(any(r == "tool_call" for r, _ in events))
        self.assertTrue(any(r == "tool_result" and "hi" in t for r, t in events))

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


class MainTests(LiteTestCase):
    def test_headless_missing_key(self):
        with mock.patch.object(lite, "load_config", return_value={}):
            with contextlib.redirect_stderr(io.StringIO()):
                code = lite.main(["do a thing"])
        self.assertEqual(code, 2)

    def test_headless_runs_task(self):
        def fake_run_task(task, steps=None, config=None, trace=False):
            self.assertEqual(config["model"], lite.DEFAULT_MODEL)
            return "task done"

        with mock.patch.dict(os.environ, {"LITECODE_API_KEY": "k"}):
            with mock.patch.object(lite, "load_config", return_value={}):
                with mock.patch.object(lite, "run_task", fake_run_task):
                    with contextlib.redirect_stdout(io.StringIO()) as out:
                        code = lite.main(["do a thing"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().strip(), "task done")

    def test_main_dispatches_tui(self):
        with mock.patch.object(lite, "run_tui", return_value=3) as fake_tui:
            code = lite.main([])
        self.assertEqual(code, 3)
        fake_tui.assert_called_once()
        self.assertIsNone(fake_tui.call_args.kwargs.get("initial_task"))

    def test_main_tui_with_task(self):
        with mock.patch.object(lite, "run_tui", return_value=0) as fake_tui:
            code = lite.main(["--tui", "some task"])
        self.assertEqual(code, 0)
        self.assertEqual(
            fake_tui.call_args.kwargs.get("initial_task"), "some task"
        )


class TuiModelTests(LiteTestCase):
    def test_set_model_with_provider_prefix(self):
        config = lite.resolve_config({})
        log = []
        lite._tui_set_model(log, config, "/model openai/gpt-4o")
        self.assertEqual(config["provider"], "openai")
        self.assertEqual(config["model"], "gpt-4o")
        self.assertTrue(any("openai/gpt-4o" in t for _, t in log))

    def test_set_model_bare_keeps_provider(self):
        config = lite.resolve_config(
            {
                "provider": "openrouter",
                "providers": {
                    "openrouter": {
                        "settings": {"baseURL": "https://openrouter.ai/api/v1"}
                    }
                },
            }
        )
        log = []
        lite._tui_set_model(log, config, "/model gpt-4o")
        self.assertEqual(config["provider"], "openrouter")
        self.assertEqual(config["model"], "gpt-4o")

    def test_set_model_without_id_reports_current(self):
        config = lite.resolve_config({"provider": "openai", "model": "gpt-4o-mini"})
        log = []
        lite._tui_set_model(log, config, "/model")
        self.assertTrue(any("current model" in t for _, t in log))


if __name__ == "__main__":
    unittest.main()
