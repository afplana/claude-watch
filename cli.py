#!/usr/bin/env python3
"""claude-watch unified CLI.

A thin dispatcher so a single `claude-watch` command (installed on PATH by the
Homebrew formula) can drive every part of the tool. Each subcommand just runs
the matching script under the system /usr/bin/python3 — no compiled binary, so
Santa evaluates the approved interpreter, exactly like the rest of claude-watch.

  claude-watch install            register hooks + start the menu bar app
  claude-watch uninstall [--purge] remove hooks + LaunchAgent (and data)
  claude-watch start|stop|restart  control the menu bar LaunchAgent
  claude-watch status              is the menu bar app running?
  claude-watch run [--demo]        run the menu bar app in the foreground
  claude-watch stats  [...]        usage analytics (see: claude-watch stats -h)
  claude-watch search [...]        history search (see: claude-watch search -h)
  claude-watch version
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON = "/usr/bin/python3"
VERSION = "0.1.0"

PLIST_LABEL = "com.claudewatch.bar"
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % PLIST_LABEL)


def _run(script, args):
    return subprocess.call([PYTHON, os.path.join(HERE, script)] + list(args))


def _launchctl(*args):
    return subprocess.call(["launchctl", *args])


def _status():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    running = any(PLIST_LABEL in line for line in out.splitlines())
    print("claude-watch menu bar app: %s" % ("running" if running else "not running"))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "help"
    rest = argv[1:]

    if cmd == "install":
        return _run("install.py", rest)
    if cmd == "uninstall":
        return _run("uninstall.py", rest)
    if cmd in ("stats", "search"):
        return _run("cw.py", [cmd] + rest)
    if cmd == "run":
        return _run("bar.py", rest)
    if cmd == "start":
        return _launchctl("load", "-w", PLIST_PATH)
    if cmd == "stop":
        return _launchctl("unload", PLIST_PATH)
    if cmd == "restart":
        _launchctl("unload", PLIST_PATH)
        return _launchctl("load", "-w", PLIST_PATH)
    if cmd == "status":
        return _status()
    if cmd in ("version", "--version", "-v"):
        print("claude-watch %s" % VERSION)
        return 0

    print(__doc__.strip())
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
