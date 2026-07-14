#!/usr/bin/env python3
"""Unit tests for the pure decision logic in bar.py.

Imports AppKit (preinstalled in /usr/bin/python3) but never starts the app.

Run:  /usr/bin/python3 test_bar.py
"""

import unittest
from datetime import datetime

import bar


class NotificationAlertTests(unittest.TestCase):
    def test_permission_with_pending_shows_command(self):
        title, text, sound = bar.notification_alert(
            "api-service", "Claude needs your permission",
            {"tool": "Bash", "detail": "rm -rf build/ && mvn clean install"},
        )
        self.assertIn("approve", title.lower())
        self.assertIn("api-service", title)
        self.assertEqual(text, "Bash: rm -rf build/ && mvn clean install")
        self.assertEqual(sound, "Ping")

    def test_permission_without_pending_falls_back_to_message(self):
        title, text, _ = bar.notification_alert("proj", "Claude needs your permission", None)
        self.assertIn("permission", title.lower())
        self.assertEqual(text, "Claude needs your permission")

    def test_pending_tool_without_detail(self):
        _, text, _ = bar.notification_alert("proj", "needs permission", {"tool": "Edit", "detail": ""})
        self.assertEqual(text, "Edit")

    def test_idle_message_is_passed_through(self):
        title, text, _ = bar.notification_alert("proj", "Claude is waiting for your input", None)
        self.assertNotIn("approve", title.lower())
        self.assertEqual(text, "Claude is waiting for your input")

    def test_empty_message_has_fallback_text(self):
        _, text, _ = bar.notification_alert("proj", "", None)
        self.assertTrue(text)


class StatusTests(unittest.TestCase):
    def test_event_status_mapping(self):
        self.assertEqual(bar.event_status("Stop"), bar.DONE)
        self.assertEqual(bar.event_status("SubagentStop"), bar.DONE)
        self.assertEqual(bar.event_status("Notification"), bar.WAITING)
        self.assertEqual(bar.event_status("SessionEnd"), bar.ENDED)
        self.assertEqual(bar.event_status("PreToolUse"), bar.ACTIVE)


class ActivityAgingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 24, 13, 30, 0)

    def _sess(self, status, last_ts):
        return {"status": status, "last_ts": last_ts}

    def test_recent_active_session_counts(self):
        s = self._sess(bar.ACTIVE, "2026-06-24T13:29:00")  # 1 min ago
        self.assertTrue(bar.is_active(s, self.now))
        self.assertEqual(bar.display_emoji(s, self.now), bar.STATUS_EMOJI[bar.ACTIVE])

    def test_stale_active_session_drops_off(self):
        s = self._sess(bar.ACTIVE, "2026-06-24T09:48:00")  # hours ago (the s1 case)
        self.assertFalse(bar.is_active(s, self.now))
        self.assertEqual(bar.display_emoji(s, self.now), bar.STATUS_EMOJI[bar.ENDED])

    def test_done_never_counts_active(self):
        s = self._sess(bar.DONE, "2026-06-24T13:29:59")
        self.assertFalse(bar.is_active(s, self.now))
        self.assertEqual(bar.display_emoji(s, self.now), bar.STATUS_EMOJI[bar.DONE])

    def test_waiting_recent_counts(self):
        s = self._sess(bar.WAITING, "2026-06-24T13:28:00")
        self.assertTrue(bar.is_active(s, self.now))

    def test_bad_timestamp_is_not_active(self):
        self.assertFalse(bar.is_active(self._sess(bar.ACTIVE, "garbage"), self.now))


class DescribeTests(unittest.TestCase):
    def test_tool_event(self):
        self.assertEqual(
            bar.describe({"event": "PreToolUse", "tool": "Bash", "detail": "ls"}), "Bash ls")

    def test_prompt_event(self):
        self.assertEqual(
            bar.describe({"event": "UserPromptSubmit", "tool": "", "detail": "hi"}), "prompt: hi")


class MuteTests(unittest.TestCase):
    def test_not_muted_by_default(self):
        self.assertFalse(bar.is_muted({}, "web-app"))

    def test_global_mute_silences_every_project(self):
        cfg = {"muted": True}
        self.assertTrue(bar.is_muted(cfg, "web-app"))
        self.assertTrue(bar.is_muted(cfg, "api-service"))

    def test_per_project_mute_only_targets_that_project(self):
        cfg = {"muted": False, "muted_projects": ["api-service"]}
        self.assertTrue(bar.is_muted(cfg, "api-service"))
        self.assertFalse(bar.is_muted(cfg, "web-app"))

    def test_toggle_project_mute_adds_and_removes(self):
        cfg = {}
        bar.toggle_project_mute(cfg, "web-app")
        self.assertEqual(cfg["muted_projects"], ["web-app"])
        bar.toggle_project_mute(cfg, "web-app")
        self.assertEqual(cfg["muted_projects"], [])


class TerminalTargetTests(unittest.TestCase):
    def test_known_terminal_maps_to_app_names(self):
        self.assertIn("Terminal", bar.terminal_app_names("Apple_Terminal"))
        self.assertIn("iTerm2", bar.terminal_app_names("iTerm.app"))

    def test_unknown_terminal_falls_back_to_raw_value(self):
        self.assertEqual(bar.terminal_app_names("SomeFutureTerm"), ["SomeFutureTerm"])

    def test_empty_terminal_yields_no_targets(self):
        self.assertEqual(bar.terminal_app_names(""), [])


class FocusPlanTests(unittest.TestCase):
    def test_iterm_uuid_extracted_from_session_id(self):
        self.assertEqual(bar.iterm_session_uuid("w0t1p0:ABC-123"), "ABC-123")
        self.assertEqual(bar.iterm_session_uuid("ABC-123"), "ABC-123")
        self.assertEqual(bar.iterm_session_uuid(""), "")

    def test_plan_prefers_iterm_when_session_id_present(self):
        kind, script = bar.focus_plan("iTerm.app", "w0t1p0:ABC-123", "")
        self.assertEqual(kind, "iterm")
        self.assertIn("ABC-123", script)

    def test_plan_uses_terminal_tty_match(self):
        kind, script = bar.focus_plan("Apple_Terminal", "", "/dev/ttys004")
        self.assertEqual(kind, "terminal")
        self.assertIn("/dev/ttys004", script)

    def test_plan_falls_back_to_app_without_identifiers(self):
        self.assertEqual(bar.focus_plan("Apple_Terminal", "", ""), ("app", None))
        self.assertEqual(bar.focus_plan("iTerm.app", "", ""), ("app", None))

    def test_plan_falls_back_to_app_for_unknown_terminal(self):
        self.assertEqual(bar.focus_plan("SomeFutureTerm", "x:y", "/dev/ttys1"),
                         ("app", None))


class SessionLabelTests(unittest.TestCase):
    def test_label_combines_project_and_title(self):
        s = {"project": "web-app", "title": "fix the rounding bug"}
        self.assertEqual(bar.session_label(s), "web-app — fix the rounding bug")

    def test_label_falls_back_to_project_without_title(self):
        self.assertEqual(bar.session_label({"project": "web-app"}), "web-app")
        self.assertEqual(bar.session_label({"project": "web-app", "title": ""}), "web-app")

    def test_label_truncates_long_title(self):
        s = {"project": "p", "title": "x" * 100}
        out = bar.session_label(s, width=20)
        self.assertLessEqual(len(out), 20)
        self.assertTrue(out.endswith("…"))


class BannerActionTests(unittest.TestCase):
    def test_pending_command_text_prefers_detail(self):
        s = {"pending": {"tool": "Bash", "detail": "rm -rf build/"}}
        self.assertEqual(bar.pending_command_text(s), "rm -rf build/")

    def test_pending_command_text_uses_tool_when_no_detail(self):
        s = {"pending": {"tool": "Edit", "detail": ""}}
        self.assertEqual(bar.pending_command_text(s), "Edit")

    def test_pending_command_text_empty_when_no_pending(self):
        self.assertEqual(bar.pending_command_text({}), "")
        self.assertEqual(bar.pending_command_text({"pending": None}), "")

    def test_snooze_seconds(self):
        self.assertEqual(bar.snooze_seconds(5), 300.0)
        self.assertEqual(bar.snooze_seconds(), 300.0)


if __name__ == "__main__":
    unittest.main()
