"""LLM calls: the dispatcher plus the openai, codex and agy backends."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

from lite.config import provider_env_key
from lite.tools import SH_TIMEOUT, TOOL

MAX_STEPS = int(os.environ.get("LITECODE_MAX_STEPS", "8"))
MESSAGE_CAP = int(os.environ.get("LITECODE_MESSAGE_CAP", "24"))

SYSTEM_PROMPT = (
    "You are a coding agent. Run shell commands with the sh tool to make "
    "the task happen, one step at a time. When the task is done, reply with "
    "a short plain-text summary of the result. Stay within the task and do "
    "not run unrelated commands."
)


def cap_messages(messages):
    """Keep the context lite: system + task, then the most recent messages."""
    if len(messages) <= MESSAGE_CAP:
        return messages
    keep = max(MESSAGE_CAP - 2, 0)
    return messages[:2] + messages[len(messages) - keep:]


def _parse_json_args(args):
    try:
        parsed = json.loads(args or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _http_json(request, error_prefix):
    """POST a JSON request; return parsed JSON, raising RuntimeError on failure."""
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"{error_prefix}: HTTP {exc.code}: {exc.read()[:400]!r}"
        )
    except OSError as exc:
        raise RuntimeError(f"{error_prefix}: {exc}")


def call_chat_completions(messages, config):
    """Call an OpenAI-compatible chat-completions endpoint (default backend).

    Returns ``(text, calls)``: the assistant's message and a list of
    ``(name, args)``, one per requested tool call.
    """
    api_key = config.get("api_key")
    base_url = config.get("base_url")
    model = config.get("model")
    provider = config.get("provider")
    if not api_key:
        raise RuntimeError(
            f"no API key: set providers.{provider}.settings.apiKey, "
            f"{provider_env_key(provider)}, or LITECODE_API_KEY"
        )
    if not base_url:
        raise RuntimeError(
            f"no base URL for provider {provider}: set "
            f"providers.{provider}.settings.baseURL or LITECODE_API_BASE"
        )
    body = {"model": model, "messages": messages, "tools": [TOOL]}
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    data = _http_json(request, "LLM request failed")
    message = data["choices"][0]["message"]
    text = message.get("content") or ""
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        calls.append((fn.get("name") or "sh", _parse_json_args(fn.get("arguments"))))
    return text, calls


# The Codex backend expects the standard Responses-API tool shape.
CODEX_TOOL = {
    "type": "function",
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
        "strict": False,
    },
}


def call_codex(messages, config):
    """Call the OpenAI Codex backend via the Responses API.

    Mirrors codex-rs (openai/codex): ``POST {base_url}/responses`` with the
    standard Responses request shape; auth is a Bearer token plus the
    ``chatgpt-account-id`` header when signed in with ChatGPT.
    """
    base_url = config.get("base_url")
    api_key = config.get("api_key")
    model = config.get("model")
    account_id = config.get("account_id")
    if not api_key:
        raise RuntimeError(
            "codex: no API key: sign in with Codex (run `codex` once), or set "
            "CODEX_API_KEY / OPENAI_API_KEY / CODEX_ACCESS_TOKEN, or set "
            "providers.codex.settings.apiKey"
        )
    if not base_url:
        raise RuntimeError(
            "codex: no base URL: set providers.codex.settings.baseURL or "
            "LITECODE_API_BASE"
        )
    # Build Responses input items: Codex sends the base instructions as the
    # first message (a user-role message in codex-rs), then the conversation.
    input_items = []
    for msg in messages:
        role = msg.get("role")
        text = msg.get("content") or ""
        if not text:
            continue
        item_role = "assistant" if role == "assistant" else "user"
        item_type = "output_text" if role == "assistant" else "input_text"
        input_items.append(
            {
                "type": "message",
                "role": item_role,
                "content": [{"type": item_type, "text": text}],
            }
        )
    body = {
        "model": model,
        "stream": False,
        "input": input_items,
        "tools": [CODEX_TOOL],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "originator": "lite",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    request = urllib.request.Request(
        base_url.rstrip("/") + "/responses",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    data = _http_json(request, "codex: LLM request failed")
    text = ""
    calls = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text += part.get("text") or ""
        elif item.get("type") == "function_call":
            calls.append(
                (item.get("name") or "sh", _parse_json_args(item.get("arguments")))
            )
    return text, calls


def _agy_binary():
    """Locate the Google Antigravity `agy` binary (AGY_BINARY, PATH, install paths)."""
    env_bin = os.environ.get("AGY_BINARY")
    if env_bin:
        return env_bin
    default = os.path.expanduser("~/.local/bin/agy")
    if sys.platform.startswith("win") and os.environ.get("LOCALAPPDATA"):
        default = os.path.join(
            os.environ["LOCALAPPDATA"], "agy", "bin", "agy.exe"
        )
    if os.path.exists(default):
        return default
    found = shutil.which("agy")
    if found:
        return found
    raise RuntimeError(
        "the agy backend requires the Google Antigravity CLI (`agy`): install "
        "it from https://antigravity.google/cli, or set AGY_BINARY to its path"
    )


def run_agy(prompt, timeout=None):
    """Run one task through ``agy -p`` (print mode) and return its output.

    agy v1.0.2 renders print-mode output only to a terminal, so we run it in
    a pseudo-terminal and capture everything it writes.
    """
    binary = _agy_binary()
    cmd = [binary, "-p", prompt, "--trust"]
    if sys.platform.startswith("win"):
        # pty is not available on Windows; capture what we can.
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or SH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return "(agy timed out)"
        return (proc.stdout or "").strip() or "(agy returned no output)"
    import pty
    import select

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=slave,
            stderr=slave,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    finally:
        os.close(slave)
    output = b""
    deadline = time.monotonic() + (timeout or SH_TIMEOUT)
    try:
        while True:
            if proc.poll() is not None:
                # Child exited: drain whatever is left on the pty.
                while True:
                    ready, _, _ = select.select([master], [], [], 0.2)
                    if not ready:
                        break
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                break
            ready, _, _ = select.select([master], [], [], min(remaining, 0.2))
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if chunk:
                    output += chunk
                else:
                    break
        proc.wait()
    finally:
        os.close(master)
    text = output.decode("utf-8", "replace").strip()
    if not text and proc.returncode != 0:
        raise RuntimeError(f"agy exited with code {proc.returncode}")
    return text or "(agy returned no output)"


def call_agy(messages, config):
    """Proxy the task to the Google Antigravity `agy` agent (lite proxy).

    The agy agent runs its own tools, so the loop ends after the first
    step: ``call_llm`` returns its reply with no tool calls.
    """
    prompt = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            prompt = msg.get("content") or ""
            break
    return run_agy(prompt), []


def call_llm(messages, config):
    """Dispatch to the provider's backend for this config."""
    ptype = config.get("type") or "openai"
    if ptype == "agy":
        return call_agy(messages, config)
    if ptype == "codex":
        return call_codex(messages, config)
    return call_chat_completions(messages, config)
