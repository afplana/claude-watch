#!/usr/bin/env python3
"""Unit tests for the pure decision logic in bar.py.

Imports AppKit (preinstalled in /usr/bin/python3) but never starts the app.

Run:  /usr/bin/python3 test_bar.py
"""

import unittest

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


class DescribeTests(unittest.TestCase):
    def test_tool_event(self):
        self.assertEqual(
            bar.describe({"event": "PreToolUse", "tool": "Bash", "detail": "ls"}), "Bash ls")

    def test_prompt_event(self):
        self.assertEqual(
            bar.describe({"event": "UserPromptSubmit", "tool": "", "detail": "hi"}), "prompt: hi")


if __name__ == "__main__":
    unittest.main()
