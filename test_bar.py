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


if __name__ == "__main__":
    unittest.main()
