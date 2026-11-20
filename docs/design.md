# Design of lite

lite runs one task through a chat LLM in a small agent loop. See the
`lite/` package (the old single `lite.py` became the package).

## The loop (`lite.agent.run_task`)

1. `messages` = the tiny system prompt + the task.
2. Call the LLM with `messages` and the single `sh` tool schema —
   `lite.llm.call_llm` dispatches by provider type:
   - `openai` (default): OpenAI-compatible chat completions
     (`POST {base_url}/chat/completions`) — `lite.llm.call_chat_completions`.
   - `codex`: the OpenAI Codex backend — `lite.llm.call_codex`.
   - `agy`: the Google Antigravity CLI — `lite.llm.call_agy`/`run_agy`.
3. If the model returns tool calls: run each one with `sh` (stdout +
   stderr, capped to `LITECODE_OUTPUT_LIMIT` chars of the tail, plus the
   exit code) and append the output as `tool` messages. Go to 2.
4. When the model replies without tool calls, that reply is the result.
   (For agy, the loop ends after the first step: agy runs its own agent
   loop, so lite proxies the task to `agy -p` and returns its reply.)
5. After `LITECODE_MAX_STEPS` steps the loop stops with "hit step limit".

## The Codex backend (`lite.llm.call_codex`)

Mirrors codex-rs (openai/codex):

- `POST {base_url}/responses` with the standard Responses request shape
  (codex-api's `ResponsesApiRequest`): `model`, `stream: false`,
  `input` items, `tools` (Responses-shaped), `tool_choice: "auto"`,
  `parallel_tool_calls: false`, `store: false`,
  `include: ["reasoning.encrypted_content"]`.
- Auth: `Authorization: Bearer <token>` + the `chatgpt-account-id` header
  when signed in with ChatGPT (verified against codex-rs
  `codex-rs/http-client/src/openai_headers.rs`).
- The system prompt is sent as the first input message (role "user"),
  then the conversation as message items; assistant replies are
  `output_text` parts.
- Credential resolution (`lite.config.resolve_config`):
  `settings.apiKey` → `CODEX_API_KEY` → `OPENAI_API_KEY` →
  `CODEX_ACCESS_TOKEN` → `LITECODE_API_KEY` → `~/.codex/auth.json`
  (`tokens.access_token`, `tokens.account_id` — Codex CLI's
  `AuthDotJson`/`TokenData` format, `codex-rs/login/src/auth/storage.rs`
  and `token_data.rs`).
- Defaults: model `gpt-5-codex` (OpenAI docs: "Default snapshot:
  gpt-5-codex"), base URL `https://chatgpt.com/backend-api/codex`.
- Codified as a test: `CodexStubTests` runs `call_codex` against a stub
  Responses server and asserts the wire shape.

## The agy backend (`lite.llm.call_agy`, `run_agy`)

- lite proxies the task: `agy -p <prompt> --trust` (print mode + trust
  the cwd).
- agy v1.0.2 renders print-mode output only to a terminal, so `run_agy`
  runs it in a pseudo-terminal (`pty.openpty()` + a `select` read loop
  with a timeout of `SH_TIMEOUT`) and captures everything it writes.
- Binary discovery: `AGY_BINARY` env → platform default
  (`~/.local/bin/agy` or `%LOCALAPPDATA%\agy\bin\agy.exe`) → `PATH`.
- Auth: browser Google Sign-In via agy's keyring; stateless CI auth via
  the `ANTIGRAVITY_API_KEY` env var (third-party-documented, unverified).
- `--trust` adds the cwd to agy's `trustedWorkspaces` so `agy -p` doesn't
  block on a trust prompt.
- Tested with a fake `agy` shell script (`AgyTests`).

## TUI (`lite.tui`)

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
- The project is fully implemented (tool, TUI, config, installer, tests,
  docs) — lite here refers to the LLM context/prompt, not the repo size.

## Model configuration (OpenCode-style)

lite reads OpenCode-style config files (JSON or JSONC with `//` and
`/* */` comments and trailing commas). Resolution:

- Config files (later wins): `~/.config/lite/opencode.json(c)`
  (`LITECODE_CONFIG_DIR`), `~/.config/opencode/opencode.json(c)`
  (`LITECODE_OPENCODE_CONFIG_DIR`), then `lite.json`/`lite.jsonc`/
  `opencode.json`/`opencode.jsonc` in the cwd.
- Precedence: project > global. Config shape (subset of OpenCode V2):

  ```jsonc
  {
    "model": "openai/gpt-4o",
    "provider": "openai",   // lite-native convenience
    "providers": {
      "openai": { "name": "OpenAI", "type": "openai", "settings": { "baseURL": "...", "apiKey": "..." } }
    }
  }
  ```

- Model: `--model` flag > `LITECODE_MODEL` > config root `model` >
  default (`gpt-4o-mini` for openai, `gpt-5-codex` for codex, cosmetic
  for agy).
- Provider: `--provider` flag > model ref provider > `LITECODE_PROVIDER`
  > config root `provider` > `openai`.
- Credentials: provider `settings.apiKey` > provider `env` list >
  `{PROVIDER}_API_KEY` > `LITECODE_API_KEY` (openai/agy); for codex:
  `settings.apiKey` > `CODEX_API_KEY` > `OPENAI_API_KEY` >
  `CODEX_ACCESS_TOKEN` > `LITECODE_API_KEY` > `~/.codex/auth.json`.

## Install (`install.sh`)

`curl .../install.sh | sh` downloads the package files into
`$LITE_INSTALL_DIR/src/lite/`, writes the launcher
`$LITE_INSTALL_DIR/lite` (which inserts `src` on `sys.path` and runs
`lite.cli.main`), and — when a terminal is available — runs
`lite --tui` (stdin if a tty, else `/dev/tty`). With no terminal (or
`LITE_INSTALL_ONLY=1`) it prints the "run it with: ..." hint instead.
Falls back to wget when curl is missing.

## GitHub Action

`.github/workflows/test.yml` runs the unit tests on push/PR to main.

## Files

- `lite/` — the package: `agent.py` (loop), `llm.py` (backends),
  `config.py` (config + resolution), `tools.py` (sh), `tui.py` (TUI),
  `cli.py` + `__main__.py` + `__init__.py`.
- `test_lite.py` — unit tests (LLM calls mocked; codex covered by a stub
  Responses server; agy by a fake `agy` CLI).
- `docs/design.md` — this document.
- `README.md` — usage, install, config summary.
- `install.sh` — `curl | sh` installer.
