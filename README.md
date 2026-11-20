# lite

lite is a lite agentic coding tool: it runs one coding task through a chat
LLM in a small agent loop. The model gets a tiny system prompt and the
task, and may call the single tool `sh` — which runs shell commands — until
it replies without tool calls; `lite` then prints the model's reply.

## Usage

```
lite "fix the failing test in test_app.py"
lite --steps 10 --trace "refactor the parser"
```

`--trace` prints each assistant reply and tool call as they happen. The
tool prints the model's final message on its own line.

## Configuration

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LITECODE_API_KEY` | — | Required. Key for the chat-completions endpoint. |
| `LITECODE_API_BASE` | `https://api.openai.com/v1` | Endpoint base URL (`POST`s go to `{base}/chat/completions`). |
| `LITECODE_MODEL` | `gpt-4o-mini` | Model to call. |
| `LITECODE_MAX_STEPS` | `8` | Maximum agent steps per run. |
| `LITECODE_OUTPUT_LIMIT` | `8000` | Tool output kept per step (chars; the tail is kept). |
| `LITECODE_MESSAGE_CAP` | `24` | Max messages kept in the prompt history. |
| `LITECODE_TIMEOUT` | `30` | Per-command timeout in seconds. |

The endpoint should speak the OpenAI Chat Completions API (function
calling). Any compatible server works.

## Tests

```
python3 -m unittest test_lite -v
```

## Docs

- [docs/design.md](docs/design.md) — how the agent loop works, and why it
  is "lite".

lite is part of the litecode repo.
