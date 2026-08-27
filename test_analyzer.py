#!/usr/bin/env python3
"""Unit tests for jsonl_util.py and analyzer.py.

Run:  /usr/bin/python3 test_analyzer.py
"""

import json
import os
import tempfile
import unittest

import analyzer
import jsonl_util as ju


SAMPLE_TRANSCRIPT = [
    {
        "type": "user",
        "timestamp": "2026-08-17T10:00:00+00:00",
        "message": {"role": "user", "content": "fix the bug"},
    },
    {
        "type": "assistant",
        "timestamp": "2026-08-17T10:00:05+00:00",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "foo.py"}},
            ],
        },
    },
]


class JsonlUtilTests(unittest.TestCase):
    def test_extract_tools(self):
        tools = ju.extract_tools(SAMPLE_TRANSCRIPT[1:])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "Edit")

    def test_parse_file_roundtrip(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            for row in SAMPLE_TRANSCRIPT:
                fh.write(json.dumps(row) + "\n")
            path = fh.name
        try:
            parsed = ju.parse_file(path)
            self.assertEqual(len(parsed), 2)
        finally:
            os.remove(path)


class IsApprovableTests(unittest.TestCase):
    def test_question_and_plan_are_approvable(self):
        self.assertTrue(analyzer.is_approvable(analyzer.QUESTION))
        self.assertTrue(analyzer.is_approvable(analyzer.PLAN_READY))

    def test_sticky_error_statuses_are_not_approvable(self):
        self.assertFalse(analyzer.is_approvable(analyzer.SESSION_LIMIT))
        self.assertFalse(analyzer.is_approvable(analyzer.API_ERROR))
        self.assertFalse(analyzer.is_approvable(analyzer.API_ERROR_OVERLOADED))

    def test_non_sticky_statuses_are_not_approvable(self):
        self.assertFalse(analyzer.is_approvable(analyzer.TASK_COMPLETE))
        self.assertFalse(analyzer.is_approvable(analyzer.REVIEW_COMPLETE))
        self.assertFalse(analyzer.is_approvable(analyzer.UNKNOWN))


class AnalyzerTests(unittest.TestCase):
    def test_task_complete_on_active_tool(self):
        self.assertEqual(analyzer.analyze_messages(SAMPLE_TRANSCRIPT), analyzer.TASK_COMPLETE)

    def test_plan_ready_on_exit_plan_mode(self):
        msgs = SAMPLE_TRANSCRIPT + [{
            "type": "assistant",
            "timestamp": "2026-08-17T10:00:10+00:00",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {}}],
            },
        }]
        self.assertEqual(analyzer.analyze_messages(msgs), analyzer.PLAN_READY)

    def test_review_complete_on_read_only(self):
        long_text = "x" * 201
        msgs = [
            SAMPLE_TRANSCRIPT[0],
            {
                "type": "assistant",
                "timestamp": "2026-08-17T10:00:05+00:00",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
                        {"type": "text", "text": long_text},
                    ],
                },
            },
        ]
        self.assertEqual(analyzer.analyze_messages(msgs), analyzer.REVIEW_COMPLETE)

    def test_session_limit(self):
        msgs = SAMPLE_TRANSCRIPT + [{
            "type": "assistant",
            "timestamp": "2026-08-17T10:00:10+00:00",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Session limit reached for this session."}],
            },
        }]
        self.assertEqual(analyzer.analyze_messages(msgs), analyzer.SESSION_LIMIT)

    def test_pretooluse_mapping(self):
        self.assertEqual(analyzer.status_for_pretooluse("ExitPlanMode"), analyzer.PLAN_READY)
        self.assertEqual(analyzer.status_for_pretooluse("AskUserQuestion"), analyzer.QUESTION)
        self.assertEqual(analyzer.status_for_pretooluse("Bash"), analyzer.UNKNOWN)

    def test_banner_for_status(self):
        title, body, sound, sticky = analyzer.banner_for_status(analyzer.PLAN_READY, "web-app [main]")
        self.assertIn("📋", title)
        self.assertTrue(sticky)


if __name__ == "__main__":
    unittest.main()
