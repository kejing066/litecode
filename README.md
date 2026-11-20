# lite

lite is a lite agentic coding tool: a full-screen chat TUI and a headless
mode, both running one coding task through a chat LLM in a small agent
loop. "Lite" means the context and prompt sent to the LLM are small: a
minimal system prompt, one `sh` tool, truncated tool output, and a capped
message history.

## Backends

- **openai** (default) — any OpenAI-compatible chat-completions endpoint
  (`providers.<id>.settings.baseURL`, env `LITECODE_API_BASE`, keys via
  `LITECODE_API_KEY` or `<PROVIDER>_API_KEY`).
- **codex** — OpenAI Codex (the Codex CLI backend): lite talks the
  Responses API at `https://chatgpt.com/backend-api/codex/responses` and
  reuses Codex's `~/.codex/auth.json` ChatGPT login.
- **agy** — Google Antigravity CLI: lite proxies the task to `agy -p`
  (print mode) and returns its reply.

## TUI

Run `lite` (or `lite --tui`) for a full-screen chat TUI: type a task +
Enter, tool calls/steps stream by, `*` assistant replies. `/model [id]`
switches models, `/help`, `/quit` (or Ctrl+C).

## Headless

```
lite "fix the failing test in test_app.py"
lite --model codex/gpt-5-codex "do the task"
lite --provider agy "do the task"
lite --steps 10 --trace "refactor the parser"
```

Prints the model's final reply. `--steps` caps the agent loop; `--trace`
prints each step while running.

## Install (`curl | sh`)

```
curl https://raw.githubusercontent.com/kejing066/litecode/main/install.sh | sh
```

Installs `lite` to `~/.local/bin` and starts its TUI. Overrides:
`LITE_INSTALL_DIR`, `LITE_REPO_URL`, `LITE_INSTALL_ONLY=1`; `AGY_BINARY`
when using the agy backend. Set `LITECODE_CONFIG_DIR` /
`LITECODE_OPENCODE_CONFIG_DIR` to redirect lite's config dirs.

## Model configuration (OpenCode-style)

Models are configured like OpenCode, with provider + model entries:

- Files read (later overrides earlier): `~/.config/lite/opencode.json(c)`
  (override dir: `LITECODE_CONFIG_DIR`), `~/.config/opencode/opencode.json(c)`
  (override: `LITECODE_OPENCODE_CONFIG_DIR`), then `lite.json`/`lite.jsonc`/
  `opencode.json`/`opencode.jsonc` in the project dir.
- Top-level `model`: `provider/model` or
  `{"providerID": ..., "model": ...}`.
- `providers.<id>` entries: `name`, `env` (credential env vars),
  `settings` (`baseURL`/`apiKey`), `models`, and — lite's addition —
  `type`: `openai` (default) | `codex` | `agy`.
- `{env:VAR}` substitutions, like OpenCode's config variables.

Example `lite.json` (JSON or JSONC):

```jsonc
{
  "model": "openai/gpt-4o",
  "providers": {
    "openai": {
      "name": "OpenAI",
      "settings": { "baseURL": "https://api.openai.com/v1", "apiKey": "sk-..." }
    },
    "openrouter": {
      "name": "OpenRouter",
      "env": ["OPENROUTER_API_KEY"],
      "settings": { "baseURL": "https://openrouter.ai/api/v1" },
      "models": { "anthropic/claude-sonnet-5.5": { "name": "Claude Sonnet" } }
    }
  }
}
```

Env: `LITECODE_MODEL`/`LITECODE_PROVIDER` override the config root;
`LITECODE_API_KEY`/`LITECODE_API_BASE` are honored; for codex:
`CODEX_API_KEY`/`OPENAI_API_KEY`/`CODEX_ACCESS_TOKEN`/`~/.codex/auth.json`.

## Module layout

```
lite/
  __init__.py  re-exports the public API (lite.sh, lite.run_task, lite.main, ...)
  cli.py       main() — argparse entry point
  agent.py     run_task() — the agent loop
  llm.py       call_llm() dispatcher: call_chat_completions(), call_codex(),
               call_agy()/run_agy() (pty runner for the agy backend)
  config.py    load_config(), resolve_config(), provider_env_key(), ...
  tools.py     sh() tool + schema
  tui.py       the chat TUI
  __main__.py  python -m lite
```

## Tests

```
python3 -m unittest test_lite -v
```

## Docs

- [docs/design.md](docs/design.md) — the agent loop, TUI, config, install.

lite is part of the litecode repo.
