#!/usr/bin/env python3
"""Notification suppression rules and cooldowns."""

import time


def _norm_msg(msg):
    msg = (msg or "").strip().rstrip(".")
    return msg.lower()


def match_suppress_filter(rule, status, branch, folder):
    """True when every non-empty rule field matches."""
    if rule.get("status") and rule["status"] != status:
        return False
    rule_branch = rule.get("git_branch")
    if rule_branch is not None and rule_branch != branch:
        return False
    rule_folder = rule.get("folder")
    if rule_folder and rule_folder not in (folder or ""):
        return False
    return True


def should_suppress(config, sess, status, message, session_state):
    """Apply suppress_filters and question cooldowns. session_state is per-session dict."""
    branch = sess.get("branch", "")
    folder = sess.get("project", "")
    for rule in config.get("suppress_filters") or []:
        if match_suppress_filter(rule, status, branch, folder):
            return True

    if status == "question":
        now = time.time()
        last_done = session_state.get("last_task_complete_ts", 0)
        cooldown = config.get("suppress_question_after_task_complete_seconds", 12)
        if cooldown > 0 and last_done and (now - last_done) < cooldown:
            return True
        last_any = session_state.get("last_notification_ts", 0)
        cooldown2 = config.get("suppress_question_after_any_notification_seconds", 7)
        if cooldown2 > 0 and last_any and (now - last_any) < cooldown2:
            return True
        last_msg = session_state.get("last_notification_message", "")
        if last_msg and _norm_msg(last_msg) == _norm_msg(message):
            if last_any and (now - last_any) < max(cooldown2, 5):
                return True
    return False


def record_notification(session_state, status, message):
    """Update per-session cooldown timestamps (mutates session_state)."""
    now = time.time()
    session_state["last_notification_ts"] = now
    session_state["last_notification_message"] = message or ""
    session_state["last_notification_status"] = status
    if status in ("task_complete", "review_complete"):
        session_state["last_task_complete_ts"] = now
