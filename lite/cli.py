"""lite command line interface."""

from __future__ import annotations

import argparse
import sys

from lite.agent import run_task
from lite.config import load_config, provider_env_key, resolve_config
from lite.llm import MAX_STEPS
from lite.tui import run_tui


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
        help=(
            "model id, or provider/model id (OpenCode-style; the codex and "
            "agy backends too)"
        ),
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="provider id (openai, codex, agy, or any configured)",
    )
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
    if config.get("type") != "agy" and not config["api_key"]:
        detail = ""
        if config.get("type") == "codex":
            detail = (
                " (or sign in with Codex: run `codex` once, or set "
                "CODEX_API_KEY/OPENAI_API_KEY, or ~/.codex/auth.json)"
            )
        print(
            f"error: no API key: set providers.{config['provider']}.settings.apiKey, "
            f"{provider_env_key(config['provider'])}, or LITECODE_API_KEY{detail}",
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
