"""The lite tool: ``sh`` — run a shell command, return truncated output + exit code."""

from __future__ import annotations

import os
import subprocess

TOOL_OUTPUT_LIMIT = int(os.environ.get("LITECODE_OUTPUT_LIMIT", "8000"))
SH_TIMEOUT = int(os.environ.get("LITECODE_TIMEOUT", "30"))

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
