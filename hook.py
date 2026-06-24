#!/usr/bin/env python3
"""claude-watch capture hook.

Registered as the command for Claude Code hook events. Reads the hook's JSON
payload from stdin, normalizes it into a flat event record, and appends one
NDJSON line to ~/.claude-watch/events-YYYY-MM-DD.ndjson.

Pure stdlib, pure capture: it never blocks, never writes to stdout (so it can't
influence the agent), and always exits 0 even on error. Runs under the system
/usr/bin/python3, so Santa evaluates the interpreter, not a new binary.
"""

import json
import os
import sys
from datetime import datetime

DATA_DIR = os.path.expanduser("~/.claude-watch")


def _short(text, limit=80):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_tool(tool_name, tool_input):
    """Human-readable one-liner describing what a tool call is doing."""
    tool_input = tool_input or {}
    if not isinstance(tool_input, dict):
        return _short(tool_input)
    if tool_name in ("Edit", "Write", "Read", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return os.path.basename(path) if path else ""
    if tool_name == "Bash":
        return _short(tool_input.get("command", ""), 120)  # keep enough to vet an approval
    if tool_name in ("Grep", "Glob"):
        return _short(tool_input.get("pattern", ""))
    if tool_name == "Task":
        return _short(tool_input.get("description", ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return _short(tool_input.get("url") or tool_input.get("query", ""))
    return ""


def normalize(raw):
    """Map a raw Claude Code hook payload to a flat event record.

    Pure function (no I/O) so it can be unit-tested.
    """
    event = raw.get("hook_event_name", "Unknown")
    cwd = raw.get("cwd", "")
    project = os.path.basename(cwd.rstrip("/")) if cwd else "(unknown)"
    tool = raw.get("tool_name", "")
    detail = ""

    if event in ("PreToolUse", "PostToolUse"):
        detail = summarize_tool(tool, raw.get("tool_input"))
    elif event == "UserPromptSubmit":
        detail = _short(raw.get("prompt", ""))
    elif event == "Notification":
        detail = _short(raw.get("message", ""))
    elif event == "SessionStart":
        detail = raw.get("source", "")
    elif event == "SessionEnd":
        detail = raw.get("reason", "")

    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": raw.get("session_id", ""),
        "project": project,
        "cwd": cwd,
        "event": event,
        "tool": tool,
        "detail": detail,
    }


def events_path(now=None):
    now = now or datetime.now()
    return os.path.join(DATA_DIR, "events-%s.ndjson" % now.strftime("%Y-%m-%d"))


def main():
    try:
        raw = json.load(sys.stdin)
    except Exception:
        return  # nothing usable on stdin; stay invisible
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        record = normalize(raw)
        with open(events_path(), "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # never crash the agent
        try:
            with open(os.path.join(DATA_DIR, "hook-errors.log"), "a") as fh:
                fh.write("%s %r\n" % (datetime.now().isoformat(), exc))
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
