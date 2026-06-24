#!/usr/bin/env python3
"""Install claude-watch.

  1. Merges the capture hook into ~/.claude/settings.json for the relevant
     Claude Code events (idempotent; backs up settings.json first).
  2. Installs a LaunchAgent so the menu bar app starts at login, and starts it now.

Run:  /usr/bin/python3 install.py
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "hook.py")
BAR = os.path.join(HERE, "bar.py")
PYTHON = "/usr/bin/python3"
HOOK_COMMAND = '%s "%s"' % (PYTHON, HOOK)

SETTINGS = os.path.expanduser("~/.claude/settings.json")
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")
PLIST_LABEL = "com.claudewatch.bar"
PLIST_PATH = os.path.join(LAUNCH_AGENTS, PLIST_LABEL + ".plist")
DATA_DIR = os.path.expanduser("~/.claude-watch")

HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SubagentStop",
    "SessionEnd",
]


def merge_settings():
    settings = {}
    if os.path.exists(SETTINGS):
        with open(SETTINGS) as fh:
            settings = json.load(fh)
        backup = SETTINGS + ".bak-claude-watch-" + datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(SETTINGS, backup)
        print("  backed up settings.json -> %s" % os.path.basename(backup))
    else:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)

    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        # drop any prior claude-watch entry so re-running stays idempotent
        entries = [e for e in entries if not _is_ours(e)]
        entries.append(
            {"matcher": "", "hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 5}]}
        )
        hooks[event] = entries

    with open(SETTINGS, "w") as fh:
        json.dump(settings, fh, indent=2)
    print("  registered hooks for: %s" % ", ".join(HOOK_EVENTS))


def _is_ours(entry):
    for h in entry.get("hooks", []):
        if "claude-watch" in h.get("command", "") and "hook.py" in h.get("command", ""):
            return True
    return False


def install_launch_agent():
    os.makedirs(LAUNCH_AGENTS, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>%s</string>
    <key>ProgramArguments</key>
    <array>
        <string>%s</string>
        <string>%s</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>%s/bar.log</string>
    <key>StandardErrorPath</key><string>%s/bar.log</string>
</dict>
</plist>
""" % (PLIST_LABEL, PYTHON, BAR, DATA_DIR, DATA_DIR)
    with open(PLIST_PATH, "w") as fh:
        fh.write(plist)
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
    result = subprocess.run(["launchctl", "load", "-w", PLIST_PATH], capture_output=True, text=True)
    if result.returncode != 0:
        print("  launchctl load warning: %s" % result.stderr.strip())
    print("  installed + started LaunchAgent %s" % PLIST_LABEL)


def main():
    print("Installing claude-watch...")
    merge_settings()
    install_launch_agent()
    print("\nDone. The 🛰️ menu bar icon should appear shortly.")
    print("Open a new Claude Code session to see the live feed.")
    print("Restart any running Claude Code sessions so they pick up the new hooks.")


if __name__ == "__main__":
    main()
    sys.exit(0)
