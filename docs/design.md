# Design of lite

lite runs one task through a chat LLM in a small agent loop. See
`lite.py`.

## The loop (`run_task`)

1. `messages` = the tiny system prompt + the task.
2. Call the LLM with `messages` and the single `sh` tool schema
   (OpenAI-compatible `POST {base_url}/chat/completions`).
3. If the model returns tool calls: run each one with `sh` (stdout +
   stderr, capped to `LITECODE_OUTPUT_LIMIT` chars of the tail, plus the
   exit code) and append the output as `tool` messages. Go to 2.
4. When the model replies without tool calls, that reply is the result.
5. After `LITECODE_MAX_STEPS` steps the loop stops with "hit step limit".

## TUI (`run_tui`)

Full-screen curses chat (no third-party dependencies):

- Header: `lite  provider/model`.
- Body: the log of `user`/`assistant`/`tool_call`/`tool_result`/`error`
  messages, wrapped and colored; the newest entries stay on screen.
- Input line at the bottom: type + Enter sends a task; `/model <id>`
  switches models (the config is re-resolved, so switching providers
  picks up their credentials); `/help`, `/quit`, Ctrl+C quit.
- While a task runs, `status` events update the hint line (e.g.
  "running…").
- Tool results are displayed truncated to 400 chars.

## Lite-ness

"Lite" means the context and prompt sent to the LLM are small:

- Lite prompt: one short system paragraph + the task.
- Lite context: tool output is truncated to its tail
  (`LITECODE_OUTPUT_LIMIT`, default 8000 chars); the message history is
  capped to `LITECODE_MESSAGE_CAP` messages (default 24) by keeping the
  system + task messages and the most recent ones.
- One tool: `sh` covers reads (`cat`, `grep`), edits and checks, so the
  tool schema is the smallest useful set.

The project is fully implemented — tool, TUI, config, installer, tests,
docs. "Lite" refers to the LLM context/prompt, not to the size of the
repo.

## Model configuration (OpenCode-style)

lite reads OpenCode-style config files (JSON or JSONC with `//` and
`/* */` comments, trailing commas):

- Global lite: `~/.config/lite/opencode.json(c)` (dir override:
  `LITECODE_CONFIG_DIR`).
- OpenCode-compat global: `~/.config/opencode/opencode.json(c)`
  (dir override: `LITECODE_OPENCODE_CONFIG_DIR`).
- Project: `lite.json`, `lite.jsonc`, `opencode.json`, `opencode.jsonc`
  (project dir = cwd; later files override earlier, and projects
  override global files).

Precedence: project > global. Config shape (subset of OpenCode V2):

```jsonc
{
  "model": "openai/gpt-4o",        // or { "providerID": "openai", "model": "gpt-4o" }
  "provider": "openai",            // lite-native convenience
  "providers": {
    "openai": {
      "name": "OpenAI",
      "env": ["OPENAI_API_KEY"],
      "settings": { "baseURL": "https://api.openai.com/v1", "apiKey": "{env:OPENAI_API_KEY}" }
    }
  }
}
```

Resolution (`resolve_config`): model: `--model` > `LITECODE_MODEL` >
config root `model` > default `openai/gpt-4o-mini`; provider: `--provider`
> model ref provider > `LITECODE_PROVIDER` > config root `provider` >
`openai`. Credentials: `settings.apiKey` > provider `env` vars >
`{PROVIDER}_API_KEY` > `LITECODE_API_KEY`. `{env:VAR}` substitutions are
resolved at load time (`load_config`).

## Install (`install.sh`)

`curl .../install.sh | sh` downloads `lite.py` from the repo, installs it
as `$LITE_INSTALL_DIR/lite` (default `~/.local/bin`), chmods it, and — when
a terminal is available — runs its TUI. With no terminal (or
`LITE_INSTALL_ONLY=1`) it prints the "run it with: ..." hint instead.
Falls back to wget when curl is missing.

## Files

- `lite.py` — the tool: agent loop + config + TUI + CLI.
- `test_lite.py` — unit tests (LLM call mocked).
- `docs/design.md` — this document.
- `README.md` — usage, install, config summary.
- `install.sh` — `curl | sh` installer.
