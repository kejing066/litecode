# Design of lite

lite runs one task through a chat LLM in a small agent loop. See
`lite.py`.

## The loop (`run_task`)

1. `messages` = the tiny system prompt + the task.
2. Call the LLM with `messages` and the single `sh` tool schema
   (OpenAI-compatible `POST {LITECODE_API_BASE}/chat/completions`).
3. If the model returns tool calls: run each one with `sh` (stdout +
   stderr, capped to `LITECODE_OUTPUT_LIMIT` chars of the tail, plus the
   exit code) and append the output as `tool` messages. Go to 2.
4. When the model replies without tool calls, that reply is the result.
5. After `LITECODE_MAX_STEPS` steps the loop stops with "hit step limit".

## Why it is "lite"

- **Lite prompt:** the system prompt is one short paragraph and the user
  message is just the task.
- **Lite context:** tool output is truncated to its tail, and the history
  is capped to `LITECODE_MESSAGE_CAP` messages — system + task, then the
  most recent ones.
- **One tool:** `sh` covers reads (`cat`, `grep`), edits and checks, so
  the tool schema is the smallest useful set.

## Files

- `lite.py` — the tool, stdlib only.
- `test_lite.py` — unit tests; the LLM call is mocked.
- `README.md` — usage and configuration.
