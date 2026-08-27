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

import analyzer
import dedup
import notify_policy
import sessionname
import webhook

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
    NSFontAttributeName,
    NSForegroundColorAttributeName,
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
    NSWorkspace,
)
from Foundation import NSAttributedString, NSObject, NSTimer
from PyObjCTools import AppHelper

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


def ago(ts, now):
    """Compact time-since string: 'just now' (<1m), 'Nm' (<1h), 'Nh'.
    Returns '' for an empty or unparseable timestamp."""
    t = parse_ts(ts)
    if t is None:
        return ""
    secs = (now - t).total_seconds()
    if secs < 60:
        return "just now"
    mins = int(secs // 60)
    if mins < 60:
        return "%dm" % mins
    return "%dh" % (mins // 60)


def _recent(sess, now, idle):
    """True if the session has activity within the idle window."""
    t = parse_ts(sess.get("last_ts", ""))
    return t is not None and (now - t).total_seconds() <= idle


def is_active(sess, now, idle=IDLE_SECONDS):
    """A session is 'actively working' if running/waiting AND recently active.
    Used to pick the live status emoji, NOT to count sessions (see is_open)."""
    if sess.get("status") not in (ACTIVE, WAITING):
        return False
    return _recent(sess, now, idle)


def is_open(sess, now, idle=IDLE_SECONDS):
    """A session is 'open' — and counted in the menu-bar number — if it's
    running, waiting, or finished-but-recent. Only SessionEnd or going idle for
    `idle` seconds drops it. Counting DONE keeps a finished-turn session ("your
    turn") on the tally instead of flickering off the instant a turn ends (#8)."""
    if sess.get("status") not in (ACTIVE, WAITING, DONE):
        return False
    return _recent(sess, now, idle)


def session_breakdown(sessions, now, idle=IDLE_SECONDS):
    """Count open sessions, split into working (running), waiting (permission),
    and done (finished its turn). 'awaiting' = waiting + done (both need you)."""
    counts = {"open": 0, "working": 0, "waiting": 0, "done": 0, "awaiting": 0}
    for sess in sessions:
        if not is_open(sess, now, idle):
            continue
        counts["open"] += 1
        status = sess.get("status")
        if status == ACTIVE:
            counts["working"] += 1
        elif status == WAITING:
            counts["waiting"] += 1
            counts["awaiting"] += 1
        else:  # DONE (is_open already excluded ENDED/stale)
            counts["done"] += 1
            counts["awaiting"] += 1
    return counts


def needs_you(sessions, now, idle=IDLE_SECONDS):
    """Open sessions that need you, most urgent first: permission (WAITING)
    before finished (DONE); within each tier, longest wait first."""
    awaiting = [s for s in sessions
                if is_open(s, now, idle) and s.get("status") in (WAITING, DONE)]
    tier = {WAITING: 0, DONE: 1}
    awaiting.sort(key=lambda s: (tier.get(s.get("status"), 2), s.get("last_ts", "")))
    return awaiting


def needs_you_row(sess, now):
    """Dropdown row for a session that needs you: emoji + label + wait time."""
    emoji = STATUS_EMOJI.get(sess.get("status"), "")
    age = ago(sess.get("last_ts", ""), now)
    suffix = " · %s" % age if age else ""
    return "%s  %s%s" % (emoji, session_label(sess), suffix)


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
    """Human label: 'project [branch] — first prompt [cat]' when available."""
    project = sess.get("project", "(unknown)")
    branch = (sess.get("branch") or "").strip()
    title = (sess.get("title") or "").strip()
    nick = sessionname.session_nickname(sess.get("session_id", ""))
    base = "%s [%s]" % (project, branch) if branch else project
    if title:
        label = "%s — %s" % (base, title)
    else:
        label = base
    if nick:
        label = "%s [%s]" % (label, nick)
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


def default_config():
    return {
        "muted": False,
        "muted_projects": [],
        "notify_on_text_response": True,
        "suppress_for_subagents": True,
        "notify_only_when_unfocused": False,
        "notify_delay_seconds": 0,
        "volume": 1.0,
        "terminal_bell": True,
        "suppress_question_after_task_complete_seconds": 12,
        "suppress_question_after_any_notification_seconds": 7,
        "suppress_filters": [],
        "sounds": {},
        "webhook": {"enabled": False, "url": "", "preset": "slack", "chat_id": "", "headers": {}},
    }


def terminal_is_focused(term_program):
    """Best-effort: True if the terminal app for this session is frontmost.

    Uses NSWorkspace's frontmost-app property rather than System Events UI
    scripting, so this needs no Accessibility permission -- at the cost of
    being app-level only (can't tell which window/tab of that app is up).
    Returns None when focus can't be determined (caller should still notify).
    """
    names = {n.lower() for n in terminal_app_names(term_program)}
    if not names:
        return None
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return None
    front_name = (front.localizedName() or "").lower()
    return front_name in names


def should_notify_desktop(config, sess):
    if not config.get("notify_only_when_unfocused"):
        return True
    focused = terminal_is_focused(sess.get("term", ""))
    if focused is None:
        return True
    return not focused


def _alert_kind_for_status(notify_status):
    if notify_status in (analyzer.TASK_COMPLETE, analyzer.REVIEW_COMPLETE):
        return "done"
    return "waiting"


def play_notification_sound(config, sound_name, status=""):
    """Play a named system sound or a per-status custom file path."""
    volume = float(config.get("volume", 1.0))
    volume = max(0.0, min(1.0, volume))
    custom = (config.get("sounds") or {}).get(status) or (config.get("sounds") or {}).get(sound_name)
    snd = None
    if custom and os.path.isfile(custom):
        snd = NSSound.alloc().initWithContentsOfFile_byReference_(custom, True)
    if snd is None and sound_name:
        snd = NSSound.soundNamed_(sound_name)
    if snd:
        snd.setVolume_(volume)
        snd.play()


def ring_terminal_bell(tty):
    """Ring the session's terminal bell (best-effort)."""
    if not tty:
        return
    try:
        with open(tty, "wb") as fh:
            fh.write(b"\a")
    except OSError:
        pass


def send_tty_return(tty):
    """Send Enter to a session TTY (best-effort; same path as the bell)."""
    if not tty:
        return
    try:
        with open(tty, "wb") as fh:
            fh.write(b"\r")
    except OSError:
        pass


def send_approval_keystroke(tty, tmux_pane=""):
    """Approve a Claude permission prompt: Enter via tmux or the session TTY."""
    if tmux_pane:
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", tmux_pane, "Enter"],
                capture_output=True, timeout=2,
            )
            return
        except Exception:
            pass
    send_tty_return(tty)


def _do_fire_banner(app, sess, sid, notify_status, title, body, sound, sticky, ev):
    term = sess.get("term")
    ts = sess.get("term_session", "")
    tty = sess.get("tty", "")
    cmd = pending_command_text(sess) if sticky else ""
    label = session_label(sess)
    show_banner(app, title, body, sound, term=term, term_session=ts, tty=tty,
                command_text=cmd, sticky=sticky, approvable=analyzer.is_approvable(notify_status),
                sid=sid, tmux_pane=sess.get("tmux_pane", ""),
                zellij_session=sess.get("zellij_session", ""),
                ghostty_surface=sess.get("ghostty_surface", ""))
    app.alerts.append({"ts": ev.get("ts", ""), "label": label, "kind": _alert_kind_for_status(notify_status)})
    play_notification_sound(app.config, sound, notify_status)
    if app.config.get("terminal_bell", True):
        ring_terminal_bell(tty)
    _send_webhook_async(app.config, notify_status, title, body)
    notify_state = sess.setdefault("notify_state", {})
    notify_policy.record_notification(notify_state, notify_status, body)


def _send_webhook_async(config, notify_status, title, body):
    """Deliver the webhook off the main thread -- urlopen(timeout=10) would
    otherwise block the AppKit run loop for up to 10s per notification."""
    def deliver():
        if not webhook.send_webhook(config, notify_status, title, body):
            wh = (config or {}).get("webhook") or {}
            if wh.get("enabled") and wh.get("url"):
                print("claude-watch: webhook delivery failed for status=%s" % notify_status)
    threading.Thread(target=deliver, daemon=True).start()


def _fire_banner(app, sess, sid, notify_status, title, body, sound, sticky, ev):
    """Gate, dedupe, optionally delay, then show a banner."""
    dedup_key = "%s:%s:%s:%s" % (sid, ev.get("event"), notify_status, title)
    if dedup.should_skip_duplicate(dedup_key):
        return
    if notify_policy.should_suppress(app.config, sess, notify_status, body, sess.get("notify_state", {})):
        return

    delay = float(app.config.get("notify_delay_seconds") or 0)
    delay = max(0.0, min(delay, 25.0))

    def deliver():
        if not should_notify_desktop(app.config, sess):
            return
        _do_fire_banner(app, sess, sid, notify_status, title, body, sound, sticky, ev)

    if delay <= 0:
        deliver()
    else:
        # show_banner (via deliver) must run on the main thread; AppHelper.callLater
        # schedules an NSTimer on the calling (main) run loop rather than a new thread.
        AppHelper.callLater(delay, deliver)


# ------------------------------------------------------------ terminal focus
# TERM_PROGRAM (set by the terminal Claude Code runs inside, captured by hook.py)
# → the localizedName(s) of the matching macOS app, so a click can raise it.
TERM_APP_MAP = {
    "Apple_Terminal": ["Terminal"],
    "iTerm.app": ["iTerm2", "iTerm"],
    "vscode": ["Code", "Visual Studio Code", "Code - Insiders"],
    "cursor": ["Cursor"],
    "Cursor": ["Cursor"],
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


def ghostty_focus_script(surface_id):
    return (
        'tell application "Ghostty"\n'
        '  set t to terminal id "%s"\n'
        '  focus t\n'
        '  activate\n'
        '  return "FOUND"\n'
        'end tell\n' % surface_id
    )


def focus_plan(term, term_session, tty, tmux_pane="", zellij_session="", ghostty_surface=""):
    """Pure: decide how to focus a session's tab. Returns (kind, payload|None)."""
    if tmux_pane:
        return ("tmux", tmux_pane)
    if zellij_session:
        return ("zellij", zellij_session)
    names = {n.lower() for n in terminal_app_names(term)}
    if "ghostty" in names and ghostty_surface:
        return ("ghostty", ghostty_focus_script(ghostty_surface))
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


def focus_tab(term, term_session, tty, tmux_pane="", zellij_session="", ghostty_surface=""):
    """Raise the exact tab/pane; fall back to app-level raise if unresolved."""
    kind, payload = focus_plan(term, term_session, tty, tmux_pane, zellij_session, ghostty_surface)
    if kind == "tmux":
        try:
            subprocess.run(["tmux", "select-pane", "-t", payload], capture_output=True, timeout=2)
            return focus_terminal(term)
        except Exception:
            return focus_terminal(term)
    if kind == "zellij":
        try:
            subprocess.run(
                ["zellij", "action", "focus-session", "--session", payload],
                capture_output=True, timeout=2,
            )
            return focus_terminal(term)
        except Exception:
            return focus_terminal(term)
    if kind == "ghostty" and _osascript(payload) == "FOUND":
        return True
    if kind in ("iterm", "terminal") and _osascript(payload) == "FOUND":
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
MAX_BANNERS = 3


def banner_wait_body(base, age):
    """Banner body with a wait-time suffix, once the wait is worth showing."""
    if age and age != "just now":
        return "%s · waiting %s" % (base, age)
    return base


class BannerPanel(NSPanel):
    """Banner window that can become key so its buttons receive clicks.

    Menu bar apps run as NSApplicationActivationPolicyAccessory; combined with
    a non-activating panel, NSButtons never get mouse-down events. This panel
    accepts key status on click; BannerView.acceptsFirstMouse_ handles the
    first click while another app is frontmost.
    """

    def canBecomeKeyWindow(self):
        return True

    def canBecomeMainWindow(self):
        return False


class BannerView(NSView):
    def acceptsFirstMouse_(self, _event):
        return True


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

    def _activate_and_focus(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if not getattr(self, "term", None):
            return
        focus_tab(
            self.term,
            getattr(self, "term_session", ""),
            getattr(self, "tty", ""),
            getattr(self, "tmux_pane", ""),
            getattr(self, "zellij_session", ""),
            getattr(self, "ghostty_surface", ""),
        )

    def focusAndDismiss_(self, sender):
        """Click handler: jump to the session's terminal, then close the banner."""
        self._activate_and_focus()
        self.dismiss_(sender)

    def approveAndDismiss_(self, sender):
        """Focus the session and send Enter to accept the permission prompt."""
        self._activate_and_focus()
        tty = getattr(self, "tty", "")
        tmux_pane = getattr(self, "tmux_pane", "")
        if tty or tmux_pane:
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.35, _make_approve_keystroke(tty, tmux_pane), "fire:", None, False)
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


class _ApproveKeystroke(NSObject):
    def fire_(self, _timer):
        send_approval_keystroke(self.tty, self.tmux_pane)


def _make_approve_keystroke(tty, tmux_pane=""):
    obj = _ApproveKeystroke.alloc().init()
    obj.tty = tty
    obj.tmux_pane = tmux_pane
    return obj


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
                    tty=p.get("tty", ""), command_text=p.get("command_text", ""),
                    sticky=p.get("sticky", False), approvable=p.get("approvable", False),
                    sid=p.get("sid"),
                    tmux_pane=p.get("tmux_pane", ""), zellij_session=p.get("zellij_session", ""),
                    ghostty_surface=p.get("ghostty_surface", ""))


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


def _banner_button_title(title, text_white, size=11):
    attrs = {
        NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(text_white, 1.0),
        NSFontAttributeName: NSFont.systemFontOfSize_(size),
    }
    return NSAttributedString.alloc().initWithString_attributes_(title, attrs)


def _banner_button(frame, title, target, action, primary=False):
    b = NSButton.alloc().initWithFrame_(frame)
    b.setBordered_(False)
    b.setBezelStyle_(0)
    b.setEnabled_(True)
    b.setTarget_(target)
    b.setAction_(action)
    b.setWantsLayer_(True)
    layer = b.layer()
    layer.setCornerRadius_(6.0)
    if primary:
        layer.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.22, 0.48, 0.98, 1.0).CGColor())
        b.setAttributedTitle_(_banner_button_title(title, 1.0))
    else:
        layer.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.90, 1.0).CGColor())
        b.setAttributedTitle_(_banner_button_title(title, 0.10))
    return b


def _banner_button_bar(width, height):
    """Slightly lighter footer strip so action buttons stand out from the panel."""
    bar = NSView.alloc().initWithFrame_(((0, 0), (width, height)))
    bar.setWantsLayer_(True)
    layer = bar.layer()
    layer.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.22, 1.0).CGColor())
    layer.setBorderWidth_(0.5)
    layer.setBorderColor_(NSColor.colorWithCalibratedWhite_alpha_(0.32, 1.0).CGColor())
    return bar


def _enforce_banner_cap(app, cap=MAX_BANNERS):
    """Keep at most `cap` banners on screen; dismiss the oldest first."""
    banners = getattr(app, "banners", [])
    while len(banners) >= cap:
        banners[0].dismiss_(None)


def show_banner(app, title, body, sound=None, term=None, term_session="", tty="",
                 command_text="", sticky=False, approvable=False, sid=None,
                 tmux_pane="", zellij_session="", ghostty_surface=""):
    """Draw our own notification banner (top-right), since macOS system
    notifications don't render on this machine. Must run on the main thread.

    Renders a row of real buttons along the bottom: Focus tab (raises the
    session's terminal tab), Copy command (only when `command_text` is
    non-empty), Snooze (re-show this same banner after a delay), Dismiss.
    App-side only — these control the app/terminal/clipboard, never Claude.

    `sticky` banners (permission prompts, session-limit/API-error) get no
    auto-dismiss timer since Claude is blocked or the session needs manual
    attention; `sid` tags which session they belong to so they can be found
    and auto-closed later. `approvable` is narrower than `sticky`: only
    questions/plans actually resolve on Enter, so only those get the Approve
    button -- session-limit/API-error banners are sticky but have nothing to
    approve, and Approve there would inject a stray keystroke into the TTY."""
    if not hasattr(app, "banners"):
        app.banners = []
    _enforce_banner_cap(app)

    vf = NSScreen.mainScreen().visibleFrame()
    index = len(app.banners)
    x = vf.origin.x + vf.size.width - BANNER_W - BANNER_MARGIN
    y = vf.origin.y + vf.size.height - BANNER_H - BANNER_MARGIN - index * (BANNER_H + BANNER_GAP)

    style = NSWindowStyleMaskBorderless
    panel = BannerPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((x, y), (BANNER_W, BANNER_H)), style, NSBackingStoreBuffered, False)
    panel.setFloatingPanel_(True)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setReleasedWhenClosed_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setWorksWhenModal_(True)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary)

    content = BannerView.alloc().initWithFrame_(((0, 0), (BANNER_W, BANNER_H)))
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
    controller.tmux_pane = tmux_pane
    controller.zellij_session = zellij_session
    controller.ghostty_surface = ghostty_surface
    controller.command_text = command_text
    controller.sticky = sticky
    controller.approvable = approvable
    controller.sid = sid
    controller.body_tf = body_tf
    controller.base_body = body
    controller.payload = {
        "title": title,
        "body": body,
        "sound": sound,
        "term": term,
        "term_session": term_session,
        "tty": tty,
        "tmux_pane": tmux_pane,
        "zellij_session": zellij_session,
        "ghostty_surface": ghostty_surface,
        "command_text": command_text,
        "sticky": sticky,
        "approvable": approvable,
        "sid": sid,
    }

    # Button row along the bottom. Only questions/plans get Approve (focus + Enter)
    # -- session-limit/API-error banners are sticky but have nothing to approve.
    if approvable:
        row = [("Approve", "approveAndDismiss:"), ("Focus tab", "focusAndDismiss:")]
    else:
        row = [("Focus tab", "focusAndDismiss:")]
    if command_text:
        row.append(("Copy command", "copyCommand:"))
    if not sticky:
        row.append(("Snooze", "snooze:"))
    row.append(("Dismiss", "dismiss:"))

    row_y = 10
    row_h = 26
    row_gap = 6
    row_x0 = 14
    row_w = BANNER_W - 2 * row_x0
    btn_w = (row_w - row_gap * (len(row) - 1)) / float(len(row))
    content.addSubview_(_banner_button_bar(BANNER_W, row_y + row_h + 10))
    for i, (label, action) in enumerate(row):
        bx = row_x0 + i * (btn_w + row_gap)
        button = _banner_button(
            ((bx, row_y), (btn_w, row_h)), label, controller, action,
            primary=(action == "approveAndDismiss:"),
        )
        content.addSubview_(button)

    panel.makeKeyAndOrderFront_(None)
    if not sticky:
        controller.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            BANNER_SECONDS, controller, "dismiss:", None, False)
    app.banners.append(controller)


def dismiss_permission_banners(app, sid):
    """Close any sticky permission banner for a session that has moved on."""
    for controller in list(getattr(app, "banners", [])):
        if getattr(controller, "sticky", False) and getattr(controller, "sid", None) == sid:
            controller.dismiss_(None)


def refresh_banner_waits(app, now):
    """Update sticky banners' bodies with the current wait time."""
    for controller in getattr(app, "banners", []):
        if not getattr(controller, "sticky", False):
            continue
        body_tf = getattr(controller, "body_tf", None)
        if body_tf is None:
            continue
        sess = app.sessions.get(getattr(controller, "sid", None))
        age = ago(sess.get("last_ts", ""), now) if sess else ""
        body_tf.setStringValue_(banner_wait_body(getattr(controller, "base_body", ""), age))


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH) as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            cfg.update(stored)
            if isinstance(stored.get("webhook"), dict):
                cfg["webhook"] = {**default_config()["webhook"], **stored["webhook"]}
    except Exception:
        pass
    return cfg


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
            "session_id": sid,
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
            "branch": ev.get("branch", ""),
            "transcript_path": ev.get("transcript_path", ""),
            "tmux_pane": ev.get("tmux_pane", ""),
            "zellij_session": ev.get("zellij_session", ""),
            "ghostty_surface": ev.get("ghostty_surface", ""),
            "notify_state": {},
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
    if ev.get("branch"):
        sess["branch"] = ev["branch"]
    if ev.get("transcript_path"):
        sess["transcript_path"] = ev["transcript_path"]
    if ev.get("tmux_pane"):
        sess["tmux_pane"] = ev["tmux_pane"]
    if ev.get("zellij_session"):
        sess["zellij_session"] = ev["zellij_session"]
    if ev.get("ghostty_surface"):
        sess["ghostty_surface"] = ev["ghostty_surface"]

    # Track the tool call awaiting a result — i.e. what a permission prompt is for.
    if event == "PreToolUse":
        sess["pending"] = {"tool": ev.get("tool", ""), "detail": ev.get("detail", "")}
    elif event == "PostToolUse":
        sess["pending"] = None

    if event == "PreToolUse" and ev.get("tool") in ("ExitPlanMode", "AskUserQuestion"):
        sess["status"] = WAITING
    else:
        sess["status"] = event_status(event)
    if sess["status"] != WAITING:
        dismiss_permission_banners(app, sid)
    sess["last_ts"] = ev.get("ts", sess["last_ts"])
    if event == "UserPromptSubmit" and not sess.get("title"):
        sess["title"] = ev.get("detail", "")
    if event not in ("SessionStart", "SessionEnd"):
        sess["events"].append(ev)

    if notify_new and not is_muted(app.config, sess["project"]) and should_notify_desktop(app.config, sess):
        if not hasattr(app, "alerts"):
            app.alerts = []
        label = session_label(sess)

        if event == "PreToolUse":
            pre_status = analyzer.status_for_pretooluse(ev.get("tool", ""))
            if pre_status != analyzer.UNKNOWN:
                title, body, sound, sticky = analyzer.banner_for_status(
                    pre_status, label, pending=sess.get("pending"))
                _fire_banner(app, sess, sid, pre_status, title, body, sound, sticky, ev)

        elif event in ("Stop", "SubagentStop"):
            if event == "SubagentStop" and app.config.get("suppress_for_subagents", True):
                pass
            else:
                transcript = ev.get("transcript_path") or sess.get("transcript_path", "")
                notify_status = analyzer.analyze_transcript(
                    transcript, app.config.get("notify_on_text_response", True))
                if notify_status == analyzer.UNKNOWN:
                    notify_status = analyzer.TASK_COMPLETE
                title, body, sound, sticky = analyzer.banner_for_status(notify_status, label)
                _fire_banner(app, sess, sid, notify_status, title, body, sound, sticky, ev)

        elif event == "Notification":
            if "permission" in (ev.get("detail") or "").lower():
                title, body, sound = notification_alert(label, ev.get("detail", ""), sess.get("pending"))
                sticky = True
                notify_status = analyzer.QUESTION
            else:
                title, body, sound, sticky = analyzer.banner_for_status(analyzer.QUESTION, label, ev.get("detail", ""))
                notify_status = analyzer.QUESTION
            _fire_banner(app, sess, sid, notify_status, title, body, sound, sticky, ev)


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
            "tmux_pane": sess.get("tmux_pane", ""),
            "zellij_session": sess.get("zellij_session", ""),
            "ghostty_surface": sess.get("ghostty_surface", ""),
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
    b = session_breakdown(sessions, now)
    needs = b["waiting"] + b["done"]
    if needs:
        title = "%s %d" % (STATUS_EMOJI[WAITING], needs)   # 🟡 N — something needs you
    elif b["open"]:
        title = "%s %d" % (ICON, b["open"])                # 🛰️ N — all working
    else:
        title = ICON                                       # 🛰️ — nothing open
    app.statusitem.button().setTitle_(title)

    menu = NSMenu.alloc().init()
    if b["open"]:
        add_item(menu, app, "Claude Code — %d open · %d working · %d awaiting you"
                 % (b["open"], b["working"], b["awaiting"]))
    else:
        add_item(menu, app, "Claude Code — no open sessions")
    menu.addItem_(NSMenuItem.separatorItem())

    waiting_list = needs_you(sessions, now)
    if waiting_list:
        add_item(menu, app, "Needs you (%d)" % len(waiting_list))
        for sess in waiting_list:
            item = add_item(menu, app, "  %s" % needs_you_row(sess, now),
                            action="focusProject:")
            item.setRepresentedObject_({
                "term": sess.get("term", ""),
                "term_session": sess.get("term_session", ""),
                "tty": sess.get("tty", ""),
                "tmux_pane": sess.get("tmux_pane", ""),
                "zellij_session": sess.get("zellij_session", ""),
                "ghostty_surface": sess.get("ghostty_surface", ""),
            })
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
            # Seed a fake awaiting session so the sticky permission banner's
            # live wait time ("· waiting Nm") has a timestamp to count from.
            self.sessions["banner-test"] = {
                "project": "api-service", "status": WAITING,
                "last_ts": datetime.now().isoformat(timespec="seconds"),
                "term": os.environ.get("TERM_PROGRAM", ""),
                "term_session": "", "tty": "", "title": "", "pending": None,
            }
            show_banner(self, "🟡 api-service — approve?",
                        "Bash: rm -rf build/ && mvn clean install", "Ping",
                        term=os.environ.get("TERM_PROGRAM", ""),
                        command_text="rm -rf build/ && mvn clean install",
                        sticky=True, approvable=True, sid="banner-test")

    def tick_(self, _timer):
        if not self.demo:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self.day:
                self.day = today
                self.path = events_file_for(today)
                self.offset = 0
        consume(self, notify_new=True)
        build_menu(self)
        refresh_banner_waits(self, datetime.now())

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
        focus_tab(
            obj.get("term", ""), obj.get("term_session", ""), obj.get("tty", ""),
            obj.get("tmux_pane", ""), obj.get("zellij_session", ""), obj.get("ghostty_surface", ""),
        )

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
