#!/usr/bin/env python3
"""Unit tests for the pure query/aggregation logic in cw.py.

Run:  /usr/bin/python3 test_cw.py
"""

import unittest

import cw


def ev(ts, project, event, tool="", detail="", session="s1"):
    return {"ts": ts, "project": project, "event": event, "tool": tool,
            "detail": detail, "session": session}


SAMPLE = [
    ev("2026-06-20T09:00:00", "farecalc", "SessionStart", session="a"),
    ev("2026-06-20T09:00:05", "farecalc", "UserPromptSubmit", detail="fix bug", session="a"),
    ev("2026-06-20T09:00:10", "farecalc", "PreToolUse", "Edit", "Fare.kt", session="a"),
    ev("2026-06-20T09:01:10", "farecalc", "PreToolUse", "Bash", "mvn test", session="a"),
    ev("2026-06-20T09:02:00", "farecalc", "Stop", session="a"),
    ev("2026-06-21T11:00:00", "quote-svc", "PreToolUse", "Edit", "Quote.kt", session="b"),
    ev("2026-06-21T11:00:30", "quote-svc", "Notification", detail="needs permission", session="b"),
]


class MatchTests(unittest.TestCase):
    def test_project_filter(self):
        self.assertTrue(cw.match_event(SAMPLE[0], project="farecalc"))
        self.assertFalse(cw.match_event(SAMPLE[0], project="quote-svc"))

    def test_tool_filter(self):
        self.assertTrue(cw.match_event(SAMPLE[2], tool="Edit"))
        self.assertFalse(cw.match_event(SAMPLE[2], tool="Bash"))

    def test_text_filter_case_insensitive(self):
        self.assertTrue(cw.match_event(SAMPLE[1], text="FIX"))
        self.assertFalse(cw.match_event(SAMPLE[1], text="deploy"))

    def test_date_window(self):
        self.assertTrue(cw.match_event(SAMPLE[0], since="2026-06-20", until="2026-06-20"))
        self.assertFalse(cw.match_event(SAMPLE[5], until="2026-06-20"))


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.s = cw.compute_stats(SAMPLE)

    def test_session_and_event_counts(self):
        self.assertEqual(self.s["total_sessions"], 2)
        self.assertEqual(self.s["total_events"], len(SAMPLE))

    def test_date_range(self):
        self.assertEqual(self.s["date_range"], ("2026-06-20", "2026-06-21"))

    def test_tool_frequency_counts_pretooluse_only(self):
        self.assertEqual(self.s["tool_frequency"], {"Edit": 2, "Bash": 1})

    def test_top_files_from_edits(self):
        self.assertEqual(self.s["top_files"], {"Fare.kt": 1, "Quote.kt": 1})

    def test_prompts_and_permissions(self):
        self.assertEqual(self.s["prompts"], 1)
        self.assertEqual(self.s["permission_requests"], 1)

    def test_active_time_uses_first_and_last(self):
        # session a spans 09:00:00 -> 09:02:00 = 120s; session b spans 30s
        self.assertEqual(self.s["total_active_seconds"], 150.0)

    def test_sessions_per_project(self):
        self.assertEqual(self.s["sessions_per_project"], {"farecalc": 1, "quote-svc": 1})


class DurationTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(cw.human_duration(45), "45s")
        self.assertEqual(cw.human_duration(125), "2m 5s")
        self.assertEqual(cw.human_duration(3700), "1h 1m")


if __name__ == "__main__":
    unittest.main()
