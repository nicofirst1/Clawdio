"""Tool classification: classify(), _classify_bash_command(). Split out
of sonifier.py; see sonifier.py for the module overview."""

from __future__ import annotations

from config import CLASS_READ, CLASS_WRITE, CLASS_EXEC, READ_BASH_CMDS, GIT_READ_SUBCOMMANDS, TOOL_CLASS


def _classify_bash_command(cmd):
    """Classify a Bash tool's shell command string into 'read' | 'exec'."""
    if not isinstance(cmd, str):
        cmd = str(cmd)
    first = cmd.strip().split()[0] if cmd.strip() else ""
    # strip a leading path component, e.g. /usr/bin/grep -> grep
    first = first.rsplit("/", 1)[-1]
    if first in READ_BASH_CMDS:
        return CLASS_READ
    if first == "git":
        parts = cmd.strip().split()
        sub = parts[1] if len(parts) > 1 else ""
        if sub in GIT_READ_SUBCOMMANDS:
            return CLASS_READ
        return CLASS_EXEC
    return CLASS_EXEC


def classify(tool_name, tool_input=None):
    """Classify a tool invocation into 'read' | 'write' | 'exec' | None.

    Returns None for tools that shouldn't produce a click at all (e.g.
    Task/Agent subagent-spawning tools -- those get the subagent chime
    instead, not a click-train timbre).
    """
    if not tool_name or not isinstance(tool_name, str):
        return None
    if tool_name in TOOL_CLASS:
        return TOOL_CLASS[tool_name]
    if tool_name == "Bash":
        cmd = tool_input.get("command") or "" if isinstance(tool_input, dict) else ""
        return _classify_bash_command(cmd)
    # Unknown tool name: fall back to exec-ish default class so it's audible.
    return CLASS_EXEC
