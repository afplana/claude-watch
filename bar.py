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
import warnings
from collections import deque
from datetime import datetime

# Harmless PyObjC noise when bridging CGColor for the banner's layer.
warnings.filterwarnings("ignore", message="PyObjCPointer created")

from AppKit import (
    NSApplication,
    NSApplicationActivateAllWindows,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSLineBreakByWordWrapping,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSPasteboard,
    NSScreen,
    NSSound,
    NSStatusBar,
    NSStatusWindowLevel,
    NSTextField,
    NSVariableStatusItemLength,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWorkspace,
)
from Foundation import NSObject, NSTimer

DATA_DIR = os.path.expanduser("~/.claude-watch")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
POLL_SECONDS = 1.0
MAX_SESSIONS_SHOWN = 8
MAX_EVENTS_PER_SESSION = 3
MAX_ALERTS_SHOWN = 5

# Menu bar glyph. Swap for any single character/emoji you like, e.g.
# "🤖", "👾" (space invader), "🦾", "⚡", "🧠".
ICON = "🛰️"

ACTIVE, WAITING, DONE, ENDED = "active", "waiting", "done", "ended"
STATUS_EMOJI = {ACTIVE: "🟢", WAITING: "🟡", DONE: "✅", ENDED: "⚪️"}

# A session with no activity for this long is no longer counted as "active".
# Closed CLI instances rarely send SessionEnd, so without this they'd linger forever.
IDLE_SECONDS = 600


# ----------------------------------------------------------------- pure helpers
def parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def is_active(sess, now, idle=IDLE_SECONDS):
    """A session counts as active only if running/waiting AND recently active."""
    if sess.get("status") not in (ACTIVE, WAITING):
        return False
    t = parse_ts(sess.get("last_ts", ""))
    return t is not None and (now - t).total_seconds() <= idle


def display_emoji(sess, now, idle=IDLE_SECONDS):
    if sess.get("status") == DONE:
        return STATUS_EMOJI[DONE]
    if is_active(sess, now, idle):
        return STATUS_EMOJI[sess["status"]]
    return STATUS_EMOJI[ENDED]  # stale / idle / ended


def pending_command_text(sess):
    """The copyable command a permission prompt is about; '' if none."""
    pending = sess.get("pending") or {}
    return pending.get("detail") or pending.get("tool") or ""


def snooze_seconds(minutes=5.0):
    return minutes * 60


def session_label(sess, width=48):
    """Human label for a session: 'project — first prompt', project-only if none."""
    project = sess.get("project", "(unknown)")
    title = (sess.get("title") or "").strip()
    if not title:
        return project
    label = "%s — %s" % (project, title)
    return label if len(label) <= width else label[: width - 1] + "…"


def recent_alerts(alerts, n=MAX_ALERTS_SHOWN):
    """The last n fired alerts, newest first."""
    return list(alerts)[-n:][::-1]


# --------------------------------------------------------------- mute helpers
def is_muted(config, project):
    """Notifications are silenced if globally muted or this project is muted."""
    if config.get("muted"):
        return True
    return project in set(config.get("muted_projects", []))


def toggle_project_mute(config, project):
    """Add/remove a project from the per-project mute list (in place)."""
    muted = list(config.get("muted_projects", []))
    if project in muted:
        muted.remove(project)
    else:
        muted.append(project)
    config["muted_projects"] = muted
    return config


# ------------------------------------------------------------ terminal focus
# TERM_PROGRAM (set by the terminal Claude Code runs inside, captured by hook.py)
# → the localizedName(s) of the matching macOS app, so a click can raise it.
TERM_APP_MAP = {
    "Apple_Terminal": ["Terminal"],
    "iTerm.app": ["iTerm2", "iTerm"],
    "vscode": ["Code", "Visual Studio Code", "Code - Insiders"],
    "ghostty": ["Ghostty"],
    "WezTerm": ["WezTerm"],
    "Hyper": ["Hyper"],
    "Tabby": ["Tabby"],
    "WarpTerminal": ["Warp"],
    "warp": ["Warp"],
    "kitty": ["kitty"],
    "alacritty": ["Alacritty"],
}


def terminal_app_names(term_program):
    """Candidate app names to match against running apps for a TERM_PROGRAM."""
    if not term_program:
        return []
    return TERM_APP_MAP.get(term_program, [term_program])


def focus_terminal(term_program):
    """Bring the terminal app Claude is running in to the front. Best-effort:
    we can raise the app, not the specific tab. Returns True if we activated one."""
    wanted = {n.lower() for n in terminal_app_names(term_program)}
    if not wanted:
        return False
    for running in NSWorkspace.sharedWorkspace().runningApplications():
        name = running.localizedName()
        if name and name.lower() in wanted:
            running.activateWithOptions_(NSApplicationActivateAllWindows)
            return True
    return False


def iterm_session_uuid(term_session):
    """ITERM_SESSION_ID looks like 'w0t1p0:UUID'; return the UUID part."""
    if not term_session:
        return ""
    return term_session.split(":")[-1]


def iterm_focus_script(uuid):
    return (
        'tell application "iTerm2"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      repeat with s in sessions of t\n'
        '        if id of s is "%s" then\n'
        '          select w\n          select t\n          select s\n'
        '          activate\n          return "FOUND"\n'
        '        end if\n'
        '      end repeat\n    end repeat\n  end repeat\n'
        'end tell\nreturn ""\n' % uuid
    )


def terminal_focus_script(tty):
    return (
        'tell application "Terminal"\n'
        '  repeat with w in windows\n'
        '    repeat with t in tabs of w\n'
        '      if tty of t is "%s" then\n'
        '        set selected of t to true\n'
        '        set frontmost of w to true\n'
        '        activate\n        return "FOUND"\n'
        '      end if\n'
        '    end repeat\n  end repeat\n'
        'end tell\nreturn ""\n' % tty
    )


def focus_plan(term, term_session, tty):
    """Pure: decide how to focus a session's tab. Returns (kind, script|None)."""
    names = {n.lower() for n in terminal_app_names(term)}
    if ({"iterm2", "iterm"} & names) and iterm_session_uuid(term_session):
        return ("iterm", iterm_focus_script(iterm_session_uuid(term_session)))
    if ("terminal" in names) and tty:
        return ("terminal", terminal_focus_script(tty))
    return ("app", None)


def _osascript(script):
    """Run AppleScript via the Apple-signed /usr/bin/osascript. Returns stdout."""
    try:
        r = subprocess.run(["/usr/bin/osascript", "-e", script],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip()
    except Exception:
        return ""


def focus_tab(term, term_session, tty):
    """Raise the exact tab; fall back to the app-level raise if unresolved."""
    kind, script = focus_plan(term, term_session, tty)
    if kind in ("iterm", "terminal") and _osascript(script) == "FOUND":
        return True
    return focus_terminal(term)


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


def notification_alert(project, message, pending):
    """Decide the (title, text, sound) for a Claude Code Notification event.

    For permission prompts the message itself is generic ("Claude needs your
    permission"), so we surface the pending tool call captured from the preceding
    PreToolUse — e.g. body "Bash: rm -rf build". Pure function, unit-tested.
    """
    if "permission" in (message or "").lower():
        if pending and pending.get("tool"):
            detail = pending.get("detail") or ""
            text = "%s: %s" % (pending["tool"], detail) if detail else pending["tool"]
            return ("🟡 %s — approve?" % project, text, "Ping")
        return ("🟡 %s — needs permission" % project, message, "Ping")
    return ("🟡 %s" % project, message or "Needs your attention.", "Submarine")


BANNER_W, BANNER_H, BANNER_MARGIN, BANNER_GAP = 360, 140, 16, 8
BANNER_SECONDS = 8.0


class BannerController(NSObject):
    """Owns one floating banner window + its auto-dismiss timer.

    Kept in app.banners so it isn't garbage-collected while on screen.
    Action methods (`dismiss_`, `focusAndDismiss_`, `copyCommand_`, `snooze_`)
    are Objective-C selectors and must each take exactly one arg (the sender).
    """

    def dismiss_(self, _sender):
        if getattr(self, "timer", None):
            self.timer.invalidate()
            self.timer = None
        if getattr(self, "panel", None):
            self.panel.orderOut_(None)
        if self in self.app.banners:
            self.app.banners.remove(self)

    def focusAndDismiss_(self, sender):
        """Click handler: jump to the session's terminal, then close the banner."""
        if getattr(self, "term", None):
            focus_tab(self.term, getattr(self, "term_session", ""), getattr(self, "tty", ""))
        self.dismiss_(sender)

    def copyCommand_(self, _sender):
        """Copy the pending command's text to the clipboard, then dismiss."""
        text = getattr(self, "command_text", "")
        if text:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.writeObjects_([text])
        self.dismiss_(_sender)

    def snooze_(self, sender):
        """Hide the banner now, then re-show the same content after a delay."""
        payload = dict(getattr(self, "payload", {}))
        app = self.app
        self.dismiss_(sender)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            snooze_seconds(5), _make_snooze_reshow(app, payload), "fire:", None, False)


class _SnoozeReshow(NSObject):
    """One-shot timer target that re-shows a snoozed banner's exact content.

    Held on the timer's target ref (strong ref from NSTimer) so it survives
    until it fires; nothing else needs to keep it alive.

    `fire_` is the only Objective-C selector (1 arg → valid arity); construction
    takes multiple args, so — per this module's PyObjC convention — it lives in
    the module-level `_make_snooze_reshow` helper below, not a classmethod on
    the NSObject subclass (PyObjC infers 0-arg selectors for methods without a
    trailing underscore, so a 2-arg `make` classmethod raises BadPrototypeError).
    """

    def fire_(self, _timer):
        p = self.payload
        show_banner(self.app, p["title"], p["body"], p.get("sound"),
                    term=p.get("term"), term_session=p.get("term_session", ""),
                    tty=p.get("tty", ""), command_text=p.get("command_text", ""))


def _make_snooze_reshow(app, payload):
    obj = _SnoozeReshow.alloc().init()
    obj.app = app
    obj.payload = payload
    return obj


def _banner_label(frame, text, size, bold, white):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    tf.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(white, 1.0))
    tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    return tf


def _banner_button(frame, title, target, action):
    b = NSButton.alloc().initWithFrame_(frame)
    b.setTitle_(title)
    b.setBezelStyle_(1)  # rounded
    b.setFont_(NSFont.systemFontOfSize_(11))
    b.setTarget_(target)
    b.setAction_(action)
    return b


def show_banner(app, title, body, sound=None, term=None, term_session="", tty="", command_text=""):
    """Draw our own notification banner (top-right), since macOS system
    notifications don't render on this machine. Must run on the main thread.

    Renders a row of real buttons along the bottom: Focus tab (raises the
    session's terminal tab), Copy command (only when `command_text` is
    non-empty), Snooze (re-show this same banner after a delay), Dismiss.
    App-side only — these control the app/terminal/clipboard, never Claude."""
    if not hasattr(app, "banners"):
        app.banners = []

    vf = NSScreen.mainScreen().visibleFrame()
    index = len(app.banners)
    x = vf.origin.x + vf.size.width - BANNER_W - BANNER_MARGIN
    y = vf.origin.y + vf.size.height - BANNER_H - BANNER_MARGIN - index * (BANNER_H + BANNER_GAP)

    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((x, y), (BANNER_W, BANNER_H)), style, NSBackingStoreBuffered, False)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setReleasedWhenClosed_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)

    content = NSView.alloc().initWithFrame_(((0, 0), (BANNER_W, BANNER_H)))
    content.setWantsLayer_(True)
    content.layer().setCornerRadius_(14.0)
    content.layer().setBackgroundColor_(
        NSColor.colorWithCalibratedWhite_alpha_(0.13, 0.96).CGColor())
    panel.setContentView_(content)

    # Top area (title/body) stays non-interactive; only the button row below
    # it responds to clicks.
    content.addSubview_(_banner_label(((18, BANNER_H - 42), (BANNER_W - 36, 28)), title, 17, True, 1.0))
    body_tf = _banner_label(((18, 46), (BANNER_W - 36, BANNER_H - 92)), body, 13.5, False, 0.92)
    body_tf.cell().setWraps_(True)
    body_tf.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
    content.addSubview_(body_tf)

    controller = BannerController.alloc().init()
    controller.app = app
    controller.panel = panel
    controller.timer = None
    controller.term = term
    controller.term_session = term_session
    controller.tty = tty
    controller.command_text = command_text
    controller.payload = {
        "title": title,
        "body": body,
        "sound": sound,
        "term": term,
        "term_session": term_session,
        "tty": tty,
        "command_text": command_text,
    }

    # Button row along the bottom: Focus tab, Copy command (only when there's
    # a pending command to copy), Snooze, Dismiss.
    row = [("Focus tab", "focusAndDismiss:")]
    if command_text:
        row.append(("Copy command", "copyCommand:"))
    row.append(("Snooze", "snooze:"))
    row.append(("Dismiss", "dismiss:"))

    row_y = 10
    row_h = 24
    row_gap = 6
    row_x0 = 18
    row_w = BANNER_W - 2 * row_x0
    btn_w = (row_w - row_gap * (len(row) - 1)) / float(len(row))
    for i, (label, action) in enumerate(row):
        bx = row_x0 + i * (btn_w + row_gap)
        button = _banner_button(((bx, row_y), (btn_w, row_h)), label, controller, action)
        content.addSubview_(button)

    panel.orderFrontRegardless()
    controller.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        BANNER_SECONDS, controller, "dismiss:", None, False)
    app.banners.append(controller)

    if sound:
        snd = NSSound.soundNamed_(sound)
        if snd:
            snd.play()


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
    event = ev["event"]
    sid = ev.get("session") or "(none)"
    sess = app.sessions.get(sid)
    if sess is None:
        sess = {
            "project": ev.get("project", "(unknown)"),
            "status": ACTIVE,
            "events": deque(maxlen=MAX_EVENTS_PER_SESSION),
            "last_ts": ev.get("ts", ""),
            "pending": None,
            "term": ev.get("term", ""),
            "term_session": ev.get("term_session", ""),
            "tty": ev.get("tty", ""),
            "cwd": ev.get("cwd", ""),
            "title": "",
        }
        app.sessions[sid] = sess
    if ev.get("project"):
        sess["project"] = ev["project"]
    if ev.get("term"):
        sess["term"] = ev["term"]
    if ev.get("term_session"):
        sess["term_session"] = ev["term_session"]
    if ev.get("tty"):
        sess["tty"] = ev["tty"]
    if ev.get("cwd"):
        sess["cwd"] = ev["cwd"]

    # Track the tool call awaiting a result — i.e. what a permission prompt is for.
    if event == "PreToolUse":
        sess["pending"] = {"tool": ev.get("tool", ""), "detail": ev.get("detail", "")}
    elif event == "PostToolUse":
        sess["pending"] = None

    sess["status"] = event_status(event)
    sess["last_ts"] = ev.get("ts", sess["last_ts"])
    if event == "UserPromptSubmit" and not sess.get("title"):
        sess["title"] = ev.get("detail", "")
    if event not in ("SessionStart", "SessionEnd"):
        sess["events"].append(ev)

    if notify_new and not is_muted(app.config, sess["project"]):
        if not hasattr(app, "alerts"):
            app.alerts = []
        project = sess["project"]
        term = sess.get("term")
        ts = sess.get("term_session", "")
        tty = sess.get("tty", "")
        label = session_label(sess)
        if event == "Stop":
            show_banner(app, "✅ %s" % label, "Claude finished — your turn.",
                        "Glass", term=term, term_session=ts, tty=tty)
            app.alerts.append({"ts": ev.get("ts", ""), "label": label,
                               "kind": "done"})
        elif event == "Notification":
            title, text, sound = notification_alert(project, ev.get("detail", ""), sess.get("pending"))
            cmd = pending_command_text(sess)
            show_banner(app, title, text, sound, term=term, term_session=ts, tty=tty, command_text=cmd)
            app.alerts.append({"ts": ev.get("ts", ""), "label": label,
                               "kind": "waiting"})


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


def project_submenu(app, sess):
    """Per-project actions: jump to its terminal, mute just this project."""
    project = sess["project"]
    term = sess.get("term", "")
    sub = NSMenu.alloc().init()

    if term:
        names = terminal_app_names(term)
        label = "Focus %s" % (names[0] if names else term)
        item = add_item(sub, app, label, action="focusProject:")
        item.setRepresentedObject_({
            "term": term,
            "term_session": sess.get("term_session", ""),
            "tty": sess.get("tty", ""),
        })
    else:
        add_item(sub, app, "Focus terminal (unknown)")

    muted = project in set(app.config.get("muted_projects", []))
    mtitle = "Unmute this project" if muted else "Mute this project"
    item = add_item(sub, app, mtitle, action="muteProject:")
    item.setRepresentedObject_(project)

    if sess.get("cwd"):
        sub.addItem_(NSMenuItem.separatorItem())
        add_item(sub, app, sess["cwd"])
    return sub


def build_menu(app):
    now = datetime.now()
    sessions = ordered_sessions(app)
    active = sum(1 for s in sessions if is_active(s, now))
    app.statusitem.button().setTitle_("%s %d" % (ICON, active) if active else ICON)

    menu = NSMenu.alloc().init()
    add_item(menu, app, "Claude Code — %d active" % active)
    menu.addItem_(NSMenuItem.separatorItem())

    if not sessions:
        add_item(menu, app, "  no sessions yet today")
    for sess in sessions[:MAX_SESSIONS_SHOWN]:
        emoji = display_emoji(sess, now)
        mark = " 🔇" if is_muted(app.config, sess["project"]) else ""
        header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "%s  %s%s" % (emoji, session_label(sess), mark), "", "")
        header.setSubmenu_(project_submenu(app, sess))
        menu.addItem_(header)
        for ev in list(sess["events"]):
            add_item(menu, app, "      %s" % describe(ev))
        menu.addItem_(NSMenuItem.separatorItem())

    alerts = recent_alerts(getattr(app, "alerts", []))
    if alerts:
        menu.addItem_(NSMenuItem.separatorItem())
        add_item(menu, app, "Recent alerts")
        for a in alerts:
            icon = STATUS_EMOJI[DONE] if a["kind"] == "done" else STATUS_EMOJI[WAITING]
            add_item(menu, app, "  %s %s" % (icon, a["label"]))

    mute_title = "Unmute notifications" if app.config.get("muted") else "Mute notifications"
    add_item(menu, app, mute_title, action="toggleMute:")
    add_item(menu, app, "Quit claude-watch", action="quit:", key="q")
    app.statusitem.setMenu_(menu)


def demo_feed(path):
    script = [
        ("SessionStart", "web-app", "", ""),
        ("UserPromptSubmit", "web-app", "", "fix the rounding bug"),
        ("PreToolUse", "web-app", "Read", "Service.kt"),
        ("PreToolUse", "web-app", "Edit", "Service.kt"),
        ("SessionStart", "api-service", "", ""),
        ("PreToolUse", "api-service", "Bash", "rm -rf build/ && mvn clean install"),
        ("Notification", "api-service", "", "Claude needs your permission"),
        ("PreToolUse", "web-app", "Bash", "mvn -q test"),
        ("Stop", "web-app", "", ""),
    ]
    term = os.environ.get("TERM_PROGRAM", "")
    for event, project, tool, detail in script:
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": "demo-" + project,
            "project": project,
            "event": event,
            "tool": tool,
            "detail": detail,
            "term": term,
        }
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        time.sleep(1.5)


# --------------------------------------------------------------- ObjC delegate
class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _notification):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.sessions = {}
        self.alerts = []
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

        if "--banner-test" in sys.argv:
            show_banner(self, "🟡 api-service — approve?",
                        "Bash: rm -rf build/ && mvn clean install", "Ping",
                        term=os.environ.get("TERM_PROGRAM", ""),
                        command_text="rm -rf build/ && mvn clean install")

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

    def muteProject_(self, sender):
        toggle_project_mute(self.config, sender.representedObject())
        save_config(self.config)
        build_menu(self)

    def focusProject_(self, sender):
        obj = sender.representedObject()
        focus_tab(obj.get("term", ""), obj.get("term_session", ""), obj.get("tty", ""))

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
