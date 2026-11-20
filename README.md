# lite

lite is a lite agentic coding tool: a full-screen chat TUI and a headless
mode, both running one coding task through a chat LLM in a small agent
loop. "Lite" means the context and prompt sent to the LLM are small: a
minimal system prompt, the single `sh` tool, truncated tool output, and a
capped message history. The project is fully implemented: tool + TUI +
OpenCode-style model configuration + `install.sh` + tests + docs.

## TUI

Run `lite` (or `lite --tui`) for a full-screen chat TUI:

- Type a task, press Enter: the model runs, tool calls show up live, and
  the final reply appears.
- `/model [id]` — show or set the current model (e.g. `openai/gpt-4o`).
- `/help`, `/quit` — help and exit (Ctrl+C also exits).

## Headless

```
lite "fix the failing test in test_app.py"
lite --steps 10 --trace "refactor the parser"
lite --model openai/gpt-4o "do the task"
```

Prints the model's final reply. `--steps` caps the agent loop; `--trace`
prints each step while running.

## Install (`curl | sh`)

```
curl https://raw.githubusercontent.com/kejing066/litecode/main/install.sh | sh
```

Installs `lite` to `~/.local/bin` and starts its TUI. Overrides:

- `LITE_INSTALL_DIR` — install dir (default `~/.local/bin`)
- `LITE_REPO_URL` — repo base URL
- `LITE_INSTALL_ONLY=1` — install without running the TUI
- `LITECODE_CONFIG_DIR` / `LITECODE_OPENCODE_CONFIG_DIR` — config dirs
  (see below)

## Model configuration (OpenCode-style)

lite configures models the way OpenCode does: `provider/model` selectors
and a `providers` map with `settings.baseURL`/`settings.apiKey`.

Files read (later overrides earlier):

1. Global lite config: `~/.config/lite/opencode.json(c)` — override the
   dir with `LITECODE_CONFIG_DIR`.
2. OpenCode-compatible global: `~/.config/opencode/opencode.json(c)` —
   override with `LITECODE_OPENCODE_CONFIG_DIR`.
3. Project files (cwd): `lite.json`, `lite.jsonc`, then
   `opencode.json`, `opencode.jsonc` (projects override global files).

Example `lite.json` (JSON or JSONC with `//` comments and trailing commas):

```jsonc
{
  "model": "openai/gpt-4o",
  "providers": {
    "openai": {
      "name": "OpenAI",
      "settings": {
        "baseURL": "https://api.openai.com/v1",
        "apiKey": "sk-..."
      }
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

Notes:

- Root `model` accepts `provider/model` or the object form
  `{"providerID": "openai", "model": "gpt-4o"}`.
- Root `provider` (lite-native convenience) sets the provider when the
  model reference has no prefix.
- `{env:VAR}` substitutions in config are resolved like OpenCode's
  config variables.
- Credentials: `settings.apiKey`, the provider's `env` list, the
  conventional `{PROVIDER}_API_KEY` (e.g. `OPENAI_API_KEY`), or
  `LITECODE_API_KEY`.

Model selection:

1. `lite --model openai/gpt-4o` (flag)
2. `LITECODE_MODEL` env
3. config root `model`
4. default `openai/gpt-4o-mini`

## Tests

```
python3 -m unittest test_lite -v
```

## Docs

- [docs/design.md](docs/design.md) — the agent loop, TUI, config, install.

lite is part of the litecode repo.
