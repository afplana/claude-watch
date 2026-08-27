#!/usr/bin/env python3
"""Tests for sessionname, dedup, notify_policy, webhook presets."""

import tempfile
import time
import unittest

import dedup
import notify_policy
import sessionname
import webhook


class SessionNameTests(unittest.TestCase):
    def test_deterministic_nickname(self):
        sid = "73b5e210-ec1a-4294-96e4-c2aecb2e1063"
        self.assertEqual(sessionname.session_nickname(sid), sessionname.session_nickname(sid))
        self.assertTrue(sessionname.session_nickname(sid))

    def test_empty_session(self):
        self.assertEqual(sessionname.session_nickname(""), "")


class DedupTests(unittest.TestCase):
    def setUp(self):
        self._orig = dedup.LOCK_DIR
        self.tmp = tempfile.mkdtemp()
        dedup.LOCK_DIR = self.tmp

    def tearDown(self):
        dedup.LOCK_DIR = self._orig

    def test_first_notification_allowed(self):
        self.assertFalse(dedup.should_skip_duplicate("test-key-1"))

    def test_duplicate_within_ttl_skipped(self):
        key = "test-key-2"
        self.assertFalse(dedup.should_skip_duplicate(key))
        self.assertTrue(dedup.should_skip_duplicate(key))


class NotifyPolicyTests(unittest.TestCase):
    def test_suppress_filter_matches_folder(self):
        cfg = {"suppress_filters": [{"folder": "ClaudeProbe", "status": "task_complete"}]}
        sess = {"project": "ClaudeProbe", "branch": "main"}
        self.assertTrue(notify_policy.should_suppress(cfg, sess, "task_complete", "done", {}))

    def test_question_cooldown_after_task_complete(self):
        cfg = {"suppress_question_after_task_complete_seconds": 60}
        sess = {"project": "p", "branch": ""}
        state = {"last_task_complete_ts": time.time()}
        self.assertTrue(notify_policy.should_suppress(cfg, sess, "question", "q?", state))


class WebhookTests(unittest.TestCase):
    def test_telegram_payload(self):
        data, ctype, _ = webhook._build_payload("telegram", "T", "B", "task_complete", {"chat_id": "99"})
        self.assertIn(b'"chat_id": "99"', data)
        self.assertEqual(ctype, "application/json")

    def test_ntfy_plaintext(self):
        data, ctype, headers = webhook._build_payload("ntfy", "Title", "Body", "x", {})
        self.assertEqual(ctype, "text/plain; charset=utf-8")
        self.assertEqual(headers["Title"], "Title")


if __name__ == "__main__":
    unittest.main()
