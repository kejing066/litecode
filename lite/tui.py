"""lite chat TUI — full-screen, curses, like OpenCode's."""

from __future__ import annotations

import os
import sys
import textwrap

from lite.agent import run_task
from lite.config import resolve_config
from lite.llm import MAX_STEPS

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
            prompt_line = f"lite> {input_line}"
            stdscr.attron(curses.A_REVERSE)
            stdscr.addnstr(h - 1, 0, prompt_line[:width], width)
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
