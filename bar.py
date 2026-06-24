#!/usr/bin/env python3
"""claude-watch menu bar app.

A native NSStatusItem menu bar app that tails the NDJSON event log written by
hook.py, tracks per-session state, renders a live feed in its dropdown, and
fires desktop notifications when a session finishes or needs your attention.

Pure system python3 + the preinstalled PyObjC bridge — no pip, no compiled
binary we ship, nothing for Santa to block. Notifications go through
/usr/bin/osascript so they always display.

Run:  /usr/bin/python3 bar.py          (normally started by the LaunchAgent)
      /usr/bin/python3 bar.py --demo    (replay synthetic events to see it work)

PyObjC note: methods on an NSObject subclass are exposed as Objective-C
selectors and must follow selector arity rules, so all multi-argument helper
logic lives in module-level functions; the delegate only carries true selectors.
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSTimer

DATA_DIR = os.path.expanduser("~/.claude-watch")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
POLL_SECONDS = 1.0
MAX_SESSIONS_SHOWN = 8
MAX_EVENTS_PER_SESSION = 3

# Menu bar glyph. Swap for any single character/emoji you like, e.g.
# "🤖", "👾" (space invader), "🦾", "⚡", "🧠".
ICON = "🛰️"

ACTIVE, WAITING, DONE, ENDED = "active", "waiting", "done", "ended"
STATUS_EMOJI = {ACTIVE: "🟢", WAITING: "🟡", DONE: "✅", ENDED: "⚪️"}


# ----------------------------------------------------------------- pure helpers
def event_status(event):
    if event in ("Stop", "SubagentStop"):
        return DONE
    if event == "Notification":
        return WAITING
    if event == "SessionEnd":
        return ENDED
    return ACTIVE


def describe(ev):
    event, tool, detail = ev["event"], ev.get("tool", ""), ev.get("detail", "")
    if event in ("PreToolUse", "PostToolUse"):
        label = tool or "tool"
        return "%s %s" % (label, detail) if detail else label
    if event == "UserPromptSubmit":
        return "prompt: %s" % detail if detail else "prompt"
    if event == "Notification":
        return detail or "needs attention"
    return event


def notify(title, text, sound="Glass"):
    """Display a macOS notification via osascript (argv-passed = injection-safe)."""
    try:
        subprocess.Popen(
            [
                "/usr/bin/osascript",
                "-e", "on run argv",
                "-e", "display notification (item 1 of argv) with title (item 2 of argv) "
                      "subtitle (item 3 of argv) sound name (item 4 of argv)",
                "-e", "end run",
                text, title, "claude-watch", sound,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def load_config():
    try:
        with open(CONFIG_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {"muted": False}


def save_config(cfg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as fh:
            json.dump(cfg, fh)
    except Exception:
        pass


def events_file_for(day):
    return os.path.join(DATA_DIR, "events-%s.ndjson" % day)


# ------------------------------------------------- state mutation (takes `app`)
def apply_event(app, ev, notify_new):
    sid = ev.get("session") or "(none)"
    sess = app.sessions.get(sid)
    if sess is None:
        sess = {
            "project": ev.get("project", "(unknown)"),
            "status": ACTIVE,
            "events": deque(maxlen=MAX_EVENTS_PER_SESSION),
            "last_ts": ev.get("ts", ""),
        }
        app.sessions[sid] = sess
    if ev.get("project"):
        sess["project"] = ev["project"]
    status = event_status(ev["event"])
    sess["status"] = status
    sess["last_ts"] = ev.get("ts", sess["last_ts"])
    if ev["event"] not in ("SessionStart", "SessionEnd"):
        sess["events"].append(ev)

    if notify_new and not app.config.get("muted"):
        project = sess["project"]
        if status == DONE and ev["event"] == "Stop":
            notify("✅ %s" % project, "Claude finished — your turn.")
        elif status == WAITING:
            notify("🟡 %s" % project, ev.get("detail") or "Needs your attention.", sound="Ping")


def consume(app, notify_new):
    """Read newly appended NDJSON lines and fold them into session state."""
    try:
        size = os.path.getsize(app.path)
    except OSError:
        return
    if size < app.offset:          # file truncated/rotated
        app.offset = 0
    if size == app.offset:
        return
    try:
        with open(app.path) as fh:
            fh.seek(app.offset)
            lines = fh.readlines()
            app.offset = fh.tell()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        apply_event(app, ev, notify_new)


def ordered_sessions(app):
    return sorted(app.sessions.values(), key=lambda s: s["last_ts"], reverse=True)


# --------------------------------------------------------------- menu rendering
def add_item(menu, target, title, action=None, key=""):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action or "", key)
    if action:
        item.setTarget_(target)
    else:
        item.setEnabled_(False)
    menu.addItem_(item)
    return item


def build_menu(app):
    sessions = ordered_sessions(app)
    active = sum(1 for s in sessions if s["status"] in (ACTIVE, WAITING))
    app.statusitem.button().setTitle_("%s %d" % (ICON, active) if active else ICON)

    menu = NSMenu.alloc().init()
    add_item(menu, app, "Claude Code — %d active" % active)
    menu.addItem_(NSMenuItem.separatorItem())

    if not sessions:
        add_item(menu, app, "  no sessions yet today")
    for sess in sessions[:MAX_SESSIONS_SHOWN]:
        emoji = STATUS_EMOJI.get(sess["status"], "•")
        add_item(menu, app, "%s  %s" % (emoji, sess["project"]))
        for ev in list(sess["events"]):
            add_item(menu, app, "      %s" % describe(ev))
        menu.addItem_(NSMenuItem.separatorItem())

    mute_title = "Unmute notifications" if app.config.get("muted") else "Mute notifications"
    add_item(menu, app, mute_title, action="toggleMute:")
    add_item(menu, app, "Quit claude-watch", action="quit:", key="q")
    app.statusitem.setMenu_(menu)


def demo_feed(path):
    script = [
        ("SessionStart", "farecalculator", "", ""),
        ("UserPromptSubmit", "farecalculator", "", "fix the surge rounding bug"),
        ("PreToolUse", "farecalculator", "Read", "FareCalculator.kt"),
        ("PreToolUse", "farecalculator", "Edit", "FareCalculator.kt"),
        ("SessionStart", "quote-service", "", ""),
        ("PreToolUse", "quote-service", "Bash", "mvn test"),
        ("PreToolUse", "farecalculator", "Bash", "mvn -q test"),
        ("Notification", "quote-service", "", "Claude needs permission to run rm"),
        ("Stop", "farecalculator", "", ""),
    ]
    for event, project, tool, detail in script:
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": "demo-" + project,
            "project": project,
            "event": event,
            "tool": tool,
            "detail": detail,
        }
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        time.sleep(1.5)


# --------------------------------------------------------------- ObjC delegate
class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.sessions = {}
        self.config = load_config()
        self.demo = "--demo" in sys.argv
        self.day = datetime.now().strftime("%Y-%m-%d")
        self.path = events_file_for("demo" if self.demo else self.day)
        self.offset = 0

        self.statusitem = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )

        if self.demo:
            open(self.path, "w").close()
            threading.Thread(target=demo_feed, args=(self.path,), daemon=True).start()
        else:
            consume(self, notify_new=False)  # seed state, no alerts on launch

        build_menu(self)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_SECONDS, self, "tick:", None, True
        )

    def tick_(self, _timer):
        if not self.demo:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.day:
                self.day = today
                self.path = events_file_for(today)
                self.offset = 0
        consume(self, notify_new=True)
        build_menu(self)

    def toggleMute_(self, _sender):
        self.config["muted"] = not self.config.get("muted", False)
        save_config(self.config)
        build_menu(self)

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(self)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no dock icon
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    app.run()


if __name__ == "__main__":
    main()
