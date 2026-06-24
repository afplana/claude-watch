#!/usr/bin/env python3
"""Unit tests for the pure event-normalization logic in hook.py.

Run:  /usr/bin/python3 test_hook.py
"""

import unittest

import hook


class NormalizeTests(unittest.TestCase):
    def test_project_derived_from_cwd(self):
        rec = hook.normalize({"hook_event_name": "SessionStart", "cwd": "/Users/me/stash/web-app"})
        self.assertEqual(rec["project"], "web-app")
        self.assertEqual(rec["event"], "SessionStart")

    def test_trailing_slash_cwd(self):
        rec = hook.normalize({"hook_event_name": "Stop", "cwd": "/Users/me/api-service/"})
        self.assertEqual(rec["project"], "api-service")

    def test_missing_cwd_is_unknown(self):
        rec = hook.normalize({"hook_event_name": "Stop"})
        self.assertEqual(rec["project"], "(unknown)")

    def test_edit_tool_detail_is_basename(self):
        rec = hook.normalize({
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/Users/me/stash/proj/src/Main.kt"},
        })
        self.assertEqual(rec["tool"], "Edit")
        self.assertEqual(rec["detail"], "Main.kt")

    def test_bash_tool_detail_is_command(self):
        rec = hook.normalize({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "mvn clean install"},
        })
        self.assertEqual(rec["detail"], "mvn clean install")

    def test_long_detail_is_truncated(self):
        rec = hook.normalize({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "x" * 200,
        })
        self.assertLessEqual(len(rec["detail"]), 80)
        self.assertTrue(rec["detail"].endswith("…"))

    def test_notification_detail(self):
        rec = hook.normalize({"hook_event_name": "Notification", "message": "needs permission"})
        self.assertEqual(rec["detail"], "needs permission")

    def test_record_has_required_fields(self):
        rec = hook.normalize({"hook_event_name": "Stop", "session_id": "abc", "cwd": "/x/proj"})
        for field in ("ts", "session", "project", "event", "tool", "detail"):
            self.assertIn(field, rec)
        self.assertEqual(rec["session"], "abc")

    def test_unknown_event_survives(self):
        rec = hook.normalize({})
        self.assertEqual(rec["event"], "Unknown")


if __name__ == "__main__":
    unittest.main()
