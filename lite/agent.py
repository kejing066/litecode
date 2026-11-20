"""The agent loop: ``run_task`` drives the backend + the ``sh`` tool until done."""

from __future__ import annotations

import json

from lite.config import load_config, resolve_config
from lite.llm import MAX_STEPS, SYSTEM_PROMPT, call_llm, cap_messages
from lite.tools import sh


def run_task(task, steps=MAX_STEPS, trace=False, on_event=None, config=None):
    """Run the agent loop for one task; return the model's final reply.

    For agy configs the task is proxied to the agy agent; ``call_llm``
    returns its reply with no tool calls, so the loop ends after the
    first step.
    """
    if config is None:
        config = resolve_config(load_config())
    if trace and on_event is None:

        def on_event(role, text):
            print(f"[{role}] {text}", flush=True)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    for step in range(1, steps + 1):
        if on_event:
            on_event("status", f"step {step}")
        text, calls = call_llm(cap_messages(messages), config)
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
            reply = text or "(no reply)"
            if on_event:
                on_event("assistant", reply)
            return reply
        for i, (name, args) in enumerate(calls, 1):
            output = sh(args.get("command", ""))
            if on_event:
                on_event("tool_call", f"{name}({args.get('command', '')})")
                on_event("tool_result", output)
            messages.append(
                {"role": "tool", "tool_call_id": f"call_{i}", "content": output}
            )
    if on_event:
        on_event("status", "hit step limit")
    return "hit step limit"
