#!/usr/bin/env python3
"""lite - a lite agentic coding tool.

Runs one coding task through a chat LLM in a small agent loop. The model
gets a tiny system prompt and the task and may call the single tool
``sh``, which runs shell commands. "Lite" means the context and prompt
sent to the LLM are small: a minimal system prompt, the single ``sh``
tool, truncated tool output, and a capped message history.

Front ends:

- headless: ``lite "fix the failing test"`` runs the loop and prints the
  model's final reply;
- a full-screen chat TUI (``lite`` or ``lite --tui``), like OpenCode:
  type tasks, watch steps/tool calls/replies stream by, ``/model``
  switches models, ``/help`` lists commands, ``/quit`` exits.

Model configuration is OpenCode-style: ``provider/model`` selectors and
``providers`` entries with ``settings.baseURL``/``settings.apiKey``, in
``lite.json``/``lite.jsonc``/``opencode.json``/``opencode.jsonc`` files
(global ``~/.config/lite/opencode.json(c)`` and
``~/.config/opencode/opencode.json(c)`` plus project files), with
``{env:VAR}`` substitution and ``LITECODE_MODEL``/``LITECODE_PROVIDER``/
``LITECODE_API_KEY``/``LITECODE_API_BASE`` overrides.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request

# --- lite settings ------------------------------------------------------------

MAX_STEPS = int(os.environ.get("LITECODE_MAX_STEPS", "8"))
TOOL_OUTPUT_LIMIT = int(os.environ.get("LITECODE_OUTPUT_LIMIT", "8000"))
MESSAGE_CAP = int(os.environ.get("LITECODE_MESSAGE_CAP", "24"))
SH_TIMEOUT = int(os.environ.get("LITECODE_TIMEOUT", "30"))

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

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


# --- the sh tool ---------------------------------------------------------------


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


# --- config (OpenCode-style) ---------------------------------------------------


def _strip_jsonc(text):
    """Strip // and /* */ comments, and trailing commas, outside strings."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
        elif ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif ch == "/" and text[i : i + 2] == "//":
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif ch == "/" and text[i : i + 2] == "/*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif ch == ",":
            # Drop commas that trail an object/array close (JSONC allows
            # trailing commas; json.loads does not).
            j = i + 1
            while j < n:
                if text[j].isspace():
                    j += 1
                elif text[j : j + 2] == "//":
                    k = text.find("\n", j)
                    j = n if k == -1 else k
                elif text[j : j + 2] == "/*":
                    k = text.find("*/", j + 2)
                    j = n if k == -1 else k + 2
                else:
                    break
            if j < n and text[j] in "}]":
                i = j
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def load_jsonc(path):
    """Load a JSONC config file; returns {} for empty files."""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = _strip_jsonc(text)
    if not text.strip():
        return {}
    return json.loads(text)


_ENV_REF = re.compile(r"\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def _env_ref(m):
    return os.environ.get(m.group(1), "")


def _substitute_env(value):
    """Resolve {env:VAR} references, like OpenCode's config variables."""
    if isinstance(value, str):
        return _ENV_REF.sub(_env_ref, value)
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    return value


def _deep_merge(base, new):
    out = dict(base)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _config_paths(project_dir=None, global_dir=None):
    """Config files, lowest precedence first (lite merges later-wins)."""
    paths = []
    if global_dir is None:
        global_dir = os.environ.get(
            "LITECODE_CONFIG_DIR",
            os.path.join(os.path.expanduser("~"), ".config", "lite"),
        )
    for name in ("opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(global_dir, name))
    opencode_dir = os.environ.get("LITECODE_OPENCODE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "opencode"
    )
    for name in ("opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(opencode_dir, name))
    if project_dir is None:
        project_dir = os.getcwd()
    for name in ("lite.json", "lite.jsonc", "opencode.json", "opencode.jsonc"):
        paths.append(os.path.join(project_dir, name))
    return paths


def load_config(project_dir=None, global_dir=None):
    """Merged OpenCode-style config: global files, then project files."""
    cfg = {}
    for path in _config_paths(project_dir, global_dir):
        if not os.path.isfile(path):
            continue
        try:
            data = load_jsonc(path)
        except (OSError, ValueError) as exc:
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            cfg = _deep_merge(cfg, data)
    return _substitute_env(cfg)


def _split_model_ref(ref):
    """Split 'provider/model': provider may be None; model may contain '/'."""
    if isinstance(ref, dict):
        model = ref.get("model") or ref.get("modelID") or None
        provider = ref.get("providerID") or ref.get("provider") or None
        return provider, model
    if not ref:
        return None, None
    if "/" in ref:
        provider, model = ref.split("/", 1)
        return (provider or None), (model or None)
    return None, ref


def provider_env_key(provider):
    """Conventional env var for a provider's key, e.g. OPENAI_API_KEY."""
    cleaned = provider.upper().replace("-", "_").replace(".", "_").replace(" ", "_")
    return f"{cleaned}_API_KEY"


def resolve_config(cfg, model=None, provider=None):
    """Effective provider/model/endpoint/key from flags, env and config.

    OpenCode-style: the model reference is ``provider/model``; the root
    ``model`` also accepts the object form ``{"providerID": ..., "model": ...}``.
    """
    if model is not None:
        cfg_model = model
    else:
        cfg_model = os.environ.get("LITECODE_MODEL", cfg.get("model", DEFAULT_MODEL))
    ref_provider, ref_model = _split_model_ref(cfg_model)
    cfg_provider = (
        provider
        or ref_provider
        or os.environ.get("LITECODE_PROVIDER")
        or cfg.get("provider")
        or DEFAULT_PROVIDER
    )
    model_id = ref_model or DEFAULT_MODEL

    entry = (cfg.get("providers") or {}).get(cfg_provider) or {}
    settings = entry.get("settings") or {}
    base_url = settings.get("baseURL") or os.environ.get("LITECODE_API_BASE")
    if base_url is None and cfg_provider == DEFAULT_PROVIDER:
        base_url = DEFAULT_BASE_URL
    api_key = settings.get("apiKey")
    for var in entry.get("env") or []:
        api_key = api_key or os.environ.get(var)
    api_key = (
        api_key
        or os.environ.get(provider_env_key(cfg_provider))
        or os.environ.get("LITECODE_API_KEY")
    )
    return {
        "raw": cfg,
        "provider": cfg_provider,
        "provider_name": entry.get("name") or cfg_provider,
        "model": model_id,
        "base_url": base_url,
        "api_key": api_key,
        "models": entry.get("models") or {},
    }


# --- LLM -----------------------------------------------------------------------


def call_llm(messages, config):
    """One OpenAI-compatible chat completion call.

    Returns ``(text, calls)``: the assistant's message and a list of
    ``(name, args)``, one per requested tool call.
    """
    api_key = config.get("api_key")
    base_url = config.get("base_url")
    model = config.get("model")
    if not api_key:
        provider = config.get("provider")
        raise RuntimeError(
            f"no API key: set providers.{provider}.settings.apiKey, "
            f"{provider_env_key(provider)}, or LITECODE_API_KEY"
        )
    if not base_url:
        raise RuntimeError(
            f"no base URL for provider {config.get('provider')}: set "
            f"providers.{config.get('provider')}.settings.baseURL or "
            "LITECODE_API_BASE"
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


# --- the agent loop --------------------------------------------------------------


def run_task(task, steps=MAX_STEPS, trace=False, on_event=None, config=None):
    """Run the agent loop for one task; return the model's final reply."""
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


# --- TUI (full-screen chat, curses; no third-party dependencies) -----------------

_ROLE_PREFIX = {
    "user": "> ",
    "assistant": "* ",
    "tool_call": "$ ",
    "tool_result": "  ",
    "error": "! ",
}


def _tui_set_model(log, config, text):
    """Handle /model <id>: switch the running config (OpenCode-style ref)."""
    parts = text.split(None, 1)
    model = parts[1].strip() if len(parts) > 1 else ""
    if not model:
        current = f"{config.get('provider')}/{config.get('model')}"
        log.append(("assistant", f"current model: {current}"))
        return
    provider = None
    if "/" in model:
        provider, model = model.split("/", 1)
    new_cfg = resolve_config(
        config.get("raw") or {},
        model=model,
        provider=provider or config.get("provider"),
    )
    config.clear()
    config.update(new_cfg)
    log.append(
        ("assistant", f"model set: {config.get('provider')}/{config.get('model')}")
    )


def _tui_main(stdscr, config, steps, initial_task):
    import curses

    stdscr.keypad(True)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    has_color = curses.has_colors()
    if has_color:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)  # header
        curses.init_pair(2, curses.COLOR_GREEN, -1)  # assistant
        curses.init_pair(3, curses.COLOR_WHITE, -1)  # user
        curses.init_pair(4, curses.COLOR_YELLOW, -1)  # tool
        curses.init_pair(5, curses.COLOR_RED, -1)  # error
        curses.init_pair(6, curses.COLOR_BLUE, -1)  # hint

    log = []
    status = ""
    input_line = ""
    if initial_task:
        log.append(("user", initial_task))

    def attr_for(role):
        if not has_color:
            return curses.A_NORMAL
        pairs = {
            "header": curses.color_pair(1) | curses.A_BOLD,
            "assistant": curses.color_pair(2),
            "user": curses.color_pair(3) | curses.A_BOLD,
            "tool_call": curses.color_pair(4),
            "tool_result": curses.color_pair(4) | curses.A_DIM,
            "error": curses.color_pair(5) | curses.A_BOLD,
            "hint": curses.color_pair(6) | curses.A_DIM,
        }
        return pairs.get(role, curses.A_NORMAL)

    def draw():
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        if h <= 0 or w <= 0:
            stdscr.refresh()
            return
        width = max(w - 1, 1)
        if h >= 1:
            attr = attr_for("header")
            header = f"lite  {config.get('provider')}/{config.get('model')}"
            stdscr.attron(attr)
            stdscr.addnstr(0, 0, header[:width], width)
            stdscr.attroff(attr)
        rows = []
        for role, text in log:
            for chunk in (
                textwrap.wrap(
                    _ROLE_PREFIX.get(role, "  ") + (text or ""),
                    width,
                    drop_whitespace=False,
                )
                or [""]
            ):
                rows.append((role, chunk))
        body_h = max(h - 3, 0)
        for i, (role, chunk) in enumerate(rows[-body_h:] if body_h else []):
            y = 1 + i
            if y >= h:
                break
            attr = attr_for(role)
            stdscr.attron(attr)
            stdscr.addnstr(y, 0, chunk[:width], width)
            stdscr.attroff(attr)
        if h >= 2:
            attr = attr_for("hint")
            hint = status or "Type a task · /help · /model <id> · /quit"
            stdscr.attron(attr)
            stdscr.addnstr(h - 2, 0, hint[:width], width)
            stdscr.attroff(attr)
        if h >= 1:
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, (f"lite> {input_line}")[:width], width)
            stdscr.attroff(curses.A_REVERSE)
        stdscr.refresh()

    def on_event(role, text):
        nonlocal status
        if role == "status":
            status = text
        elif role == "assistant":
            log.append(("assistant", text))
        elif role == "tool_call":
            log.append(("tool_call", text))
        elif role == "tool_result":
            shown = text if len(text) <= 400 else text[:400] + " ...[more]"
            log.append(("tool_result", shown))
        elif role == "error":
            log.append(("error", text))
        draw()

    def run(task):
        nonlocal status
        status = "running…"
        draw()
        try:
            run_task(task, steps=steps, on_event=on_event, config=config)
        except Exception as exc:
            log.append(("error", str(exc)))
        status = ""
        draw()

    if initial_task:
        run(initial_task)

    while True:
        draw()
        try:
            ch = stdscr.getch()
        except curses.error:
            continue
        if ch == curses.KEY_ENTER or ch in (10, 13):
            task = input_line.strip()
            input_line = ""
            if not task:
                continue
            cmd = task.lower()
            if cmd in ("/quit", "/exit", "/q"):
                break
            if cmd.startswith("/help"):
                log.append(("assistant", "commands: /model <id>, /help, /quit"))
                continue
            if cmd.startswith("/model"):
                _tui_set_model(log, config, task)
                continue
            log.append(("user", task))
            run(task)
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            input_line = input_line[:-1]
        elif ch == curses.KEY_RESIZE:
            continue
        elif 32 <= ch < 256 or ch == 9:
            input_line += chr(ch)
        else:
            continue


def run_tui(config, steps=MAX_STEPS, initial_task=None):
    """Run the interactive chat TUI (curses; no third-party dependencies)."""
    import curses

    if not (sys.stdin.isatty() or os.path.exists("/dev/tty")):
        print(
            "error: the lite TUI needs an interactive terminal",
            file=sys.stderr,
        )
        return 2
    try:
        curses.wrapper(_tui_main, config, steps, initial_task)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        print(f"error: lite TUI failed to start: {exc}", file=sys.stderr)
        return 1
    return 0


# --- CLI -------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="lite",
        description=(
            "lite: a lite agentic coding tool that runs a task through the LLM."
        ),
    )
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="the coding task to run (omit to open the TUI)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="open the chat TUI (like `lite` with no task)",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="ID",
        help="model id, or provider/model id (OpenCode-style)",
    )
    parser.add_argument("--provider", default=None, help="provider id")
    parser.add_argument(
        "--steps",
        type=int,
        default=MAX_STEPS,
        help="max agent steps (default: %(default)s)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="print each step while running headless",
    )
    args = parser.parse_args(argv)

    config = resolve_config(load_config(), model=args.model, provider=args.provider)
    if args.tui or args.task is None:
        try:
            return run_tui(
                config,
                steps=args.steps,
                initial_task=args.task if args.tui else None,
            )
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    if not config["api_key"]:
        print(
            "error: no API key: set providers."
            f"{config['provider']}.settings.apiKey, "
            f"{provider_env_key(config['provider'])}, or LITECODE_API_KEY",
            file=sys.stderr,
        )
        return 2
    try:
        result = run_task(
            args.task, steps=args.steps, config=config, trace=args.trace
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
