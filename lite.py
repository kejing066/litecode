#!/usr/bin/env python3
"""lite - a lite agentic coding tool.

Runs one coding task through a chat LLM in a small agent loop. The model
is given a tiny system prompt and the task, and may call the single tool
``sh``, which runs a shell command and returns its output. When the model
replies without tool calls, the run is finished and its reply is printed.

Lite by design: the prompt is minimal, tool output is truncated, and the
message history is capped, so the prompt and the context stay small.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# --- configuration (overridable by environment) ----------------------------

API_KEY = os.environ.get("LITECODE_API_KEY")
API_BASE = os.environ.get("LITECODE_API_BASE", "https://api.openai.com/v1")
MODEL = os.environ.get("LITECODE_MODEL", "gpt-4o-mini")
MAX_STEPS = int(os.environ.get("LITECODE_MAX_STEPS", "8"))
TOOL_OUTPUT_LIMIT = int(os.environ.get("LITECODE_OUTPUT_LIMIT", "8000"))
MESSAGE_CAP = int(os.environ.get("LITECODE_MESSAGE_CAP", "24"))
SH_TIMEOUT = int(os.environ.get("LITECODE_TIMEOUT", "30"))

SYSTEM_PROMPT = (
    "You are a coding agent. Run shell commands with the sh tool to make "
    "the task happen, one step at a time. When the task is done, reply with "
    "a short plain-text summary of the result. Stay within the task and do "
    "not run unrelated commands."
)

TOOL = {
    "type": "function",
    "function": {
        "name": "sh",
        "description": "Run a shell command in the current directory; returns "
        "its stdout+stderr and the exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def sh(command: str) -> str:
    """Run one shell command; return truncated output with the exit code."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {SH_TIMEOUT}s"
    text = (proc.stdout or "") + (proc.stderr or "")
    if len(text) > TOOL_OUTPUT_LIMIT:
        text = (
            f"...[truncated to the last {TOOL_OUTPUT_LIMIT} chars]\n"
            + text[-TOOL_OUTPUT_LIMIT:]
        )
    return f"{text}\n[exit code: {proc.returncode}]"


def call_llm(messages):
    """One OpenAI-compatible chat completion.

    Returns ``(text, calls)``: the assistant's message and a list of
    ``(name, args)``, one per requested tool call.
    """
    if not API_KEY:
        raise RuntimeError("LITECODE_API_KEY is not set")
    body = {"model": MODEL, "messages": messages, "tools": [TOOL]}
    request = urllib.request.Request(
        API_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"LLM request failed: HTTP {exc.code}: {exc.read()[:400]!r}"
        )
    except OSError as exc:
        raise RuntimeError(f"LLM request failed: {exc}")
    message = data["choices"][0]["message"]
    text = message.get("content") or ""
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append((fn.get("name") or "sh", args))
    return text, calls


def cap_messages(messages):
    """Keep the context lite: system + task, then the most recent messages."""
    if len(messages) <= MESSAGE_CAP:
        return messages
    keep = max(MESSAGE_CAP - 2, 0)
    return messages[:2] + messages[len(messages) - keep:]


def run_task(task, steps=MAX_STEPS, trace=False):
    """Run the agent loop for one task; return the model's final reply."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    for step in range(1, steps + 1):
        text, calls = call_llm(cap_messages(messages))
        if trace:
            print(f"[step {step}] reply: {text!r}", flush=True)
        assistant = {"role": "assistant", "content": text or None}
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": f"call_{i}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for i, (name, args) in enumerate(calls, 1)
            ]
        messages.append(assistant)
        if not calls:
            return text or "(no reply)"
        for i, (name, args) in enumerate(calls, 1):
            if trace:
                print(f"[step {step}] {name}({args.get('command')})", flush=True)
            output = sh(args.get("command", ""))
            if trace:
                print(f"[step {step}] -> {output!r}", flush=True)
            messages.append(
                {"role": "tool", "tool_call_id": f"call_{i}", "content": output}
            )
    return "hit step limit"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="lite",
        description=(
            "lite: run a coding task through the LLM with a lite prompt "
            "and context."
        ),
    )
    parser.add_argument("task", help="the coding task to run")
    parser.add_argument(
        "--steps",
        type=int,
        default=MAX_STEPS,
        help="max agent steps (default: %(default)s)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="print each assistant reply and tool call",
    )
    args = parser.parse_args(argv)

    if not API_KEY:
        print(
            "error: LITECODE_API_KEY is not set "
            "(see README for how to configure lite)",
            file=sys.stderr,
        )
        return 2
    try:
        result = run_task(args.task, steps=args.steps, trace=args.trace)
    except Exception as exc:  # report errors without a traceback dump
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
