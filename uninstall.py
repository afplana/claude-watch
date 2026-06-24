#!/usr/bin/env python3
"""Uninstall claude-watch — cleanly reverses install.py (unlike Masko).

  1. Removes the capture hook entries from ~/.claude/settings.json (backs up first).
  2. Stops + removes the LaunchAgent.
  3. Leaves ~/.claude-watch data in place unless --purge is passed.

Run:  /usr/bin/python3 uninstall.py [--purge]
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

SETTINGS = os.path.expanduser("~/.claude/settings.json")
PLIST_LABEL = "com.claudewatch.bar"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % PLIST_LABEL)
DATA_DIR = os.path.expanduser("~/.claude-watch")


def _is_ours(entry):
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if "claude-watch" in cmd and "hook.py" in cmd:
            return True
    return False


def clean_settings():
    if not os.path.exists(SETTINGS):
        return
    with open(SETTINGS) as fh:
        settings = json.load(fh)
    backup = SETTINGS + ".bak-claude-watch-" + datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(SETTINGS, backup)

    hooks = settings.get("hooks", {})
    for event in list(hooks.keys()):
        kept = [e for e in hooks[event] if not _is_ours(e)]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)

    with open(SETTINGS, "w") as fh:
        json.dump(settings, fh, indent=2)
    print("  removed claude-watch hooks from settings.json (backup: %s)" % os.path.basename(backup))


def remove_launch_agent():
    if os.path.exists(PLIST_PATH):
        subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True)
        os.remove(PLIST_PATH)
        print("  stopped + removed LaunchAgent %s" % PLIST_LABEL)


def main():
    print("Uninstalling claude-watch...")
    clean_settings()
    remove_launch_agent()
    if "--purge" in sys.argv:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        print("  purged %s" % DATA_DIR)
    else:
        print("  left event data in %s (use --purge to delete)" % DATA_DIR)
    print("Done.")


if __name__ == "__main__":
    main()
    sys.exit(0)
