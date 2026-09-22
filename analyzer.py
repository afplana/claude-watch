#!/usr/bin/env python3
"""Claude Code stop-hook status analyzer (stdlib only).

State machine ported from claude-notifications-go/internal/analyzer.
"""

import jsonl_util as ju

# Notification status constants (match claude-notifications-go)
TASK_COMPLETE = "task_complete"
REVIEW_COMPLETE = "review_complete"
QUESTION = "question"
PLAN_READY = "plan_ready"
SESSION_LIMIT = "session_limit_reached"
API_ERROR = "api_error"
API_ERROR_OVERLOADED = "api_error_overloaded"
UNKNOWN = "unknown"

ACTIVE_TOOLS = ("Write", "Edit", "Bash", "NotebookEdit", "SlashCommand", "KillShell")
READ_LIKE_TOOLS = ("Read", "Grep", "Glob")
RECENT_WINDOW = 15
REVIEW_TEXT_MIN = 200

APPROVABLE_STATUSES = (QUESTION, PLAN_READY)

STATUS_META = {
    TASK_COMPLETE: {"emoji": "✅", "title": "Completed", "sound": "Glass", "sticky": False},
    REVIEW_COMPLETE: {"emoji": "🔍", "title": "Review", "sound": "Glass", "sticky": False},
    QUESTION: {"emoji": "❓", "title": "Question", "sound": "Ping", "sticky": True},
    PLAN_READY: {"emoji": "📋", "title": "Plan ready", "sound": "Ping", "sticky": True},
    SESSION_LIMIT: {"emoji": "⏱️", "title": "Session limit", "sound": "Basso", "sticky": True},
    API_ERROR: {"emoji": "🔴", "title": "API error", "sound": "Basso", "sticky": True},
    API_ERROR_OVERLOADED: {"emoji": "🔴", "title": "API error", "sound": "Basso", "sticky": True},
}


def _contains_ignore_case(text, needle):
    return needle.lower() in (text or "").lower()


def detect_session_limit(messages):
    texts = ju.extract_text_from_messages(ju.get_last_assistant_messages(messages, 3))
    for text in texts:
        if _contains_ignore_case(text, "session limit reached") or _contains_ignore_case(
            text, "session limit has been reached"
        ):
            return True
    return False


def detect_api_error(messages):
    if not ju.has_recent_api_error(messages):
        return UNKNOWN
    errors = ju.get_last_api_error_messages(messages, 3)
    if not errors:
        return API_ERROR_OVERLOADED
    last = errors[-1]
    if last.get("error") == "authentication_failed":
        return API_ERROR
    texts = ju.extract_text_from_messages([last])
    for text in texts:
        if _contains_ignore_case(text, "401") and (
            _contains_ignore_case(text, "authentication_error") or _contains_ignore_case(text, "run /login")
        ):
            return API_ERROR
    return API_ERROR_OVERLOADED


def analyze_messages(messages, notify_on_text_response=True):
    """Determine notification status from parsed transcript messages."""
    if not messages:
        return UNKNOWN
    if detect_session_limit(messages):
        return SESSION_LIMIT
    api = detect_api_error(messages)
    if api != UNKNOWN:
        return api

    user_ts = ju.get_last_user_timestamp(messages)
    filtered = ju.filter_messages_after_timestamp(messages, user_ts)
    if not filtered:
        return UNKNOWN

    recent = filtered[-RECENT_WINDOW:] if len(filtered) > RECENT_WINDOW else filtered
    tools = ju.extract_tools(recent)

    if tools:
        last_tool = ju.get_last_tool(tools)
        if last_tool == "ExitPlanMode":
            return PLAN_READY
        if last_tool == "AskUserQuestion":
            return QUESTION

        exit_pos = ju.find_tool_position(tools, "ExitPlanMode")
        if exit_pos >= 0 and ju.count_tools_after_position(tools, exit_pos) > 0:
            return TASK_COMPLETE

        if ju.count_tools_by_names(tools, READ_LIKE_TOOLS) >= 1 and not ju.has_any_tool(tools, ACTIVE_TOOLS):
            if len(ju.extract_recent_text(recent, 5)) > REVIEW_TEXT_MIN:
                return REVIEW_COMPLETE

        if ju.has_any_tool(tools, ACTIVE_TOOLS):
            return TASK_COMPLETE
        return TASK_COMPLETE

    if notify_on_text_response:
        return TASK_COMPLETE
    return UNKNOWN


def analyze_transcript(path, notify_on_text_response=True):
    """Load a transcript file and return a status string."""
    return analyze_messages(ju.parse_file(path), notify_on_text_response)


def status_for_pretooluse(tool_name):
    if tool_name == "ExitPlanMode":
        return PLAN_READY
    if tool_name == "AskUserQuestion":
        return QUESTION
    return UNKNOWN


def is_approvable(status):
    """True when Enter/Approve actually resolves something Claude is blocked
    on (a question or plan). Session-limit/API-error banners are sticky but
    have nothing to approve -- Approve must not show for those."""
    return status in APPROVABLE_STATUSES


def banner_for_status(status, label, body="", pending=None):
    """Build (title, body, sound, sticky) for a detected status."""
    meta = STATUS_META.get(status, STATUS_META[TASK_COMPLETE])
    title = "%s %s" % (meta["emoji"], label)
    if status == QUESTION and pending and pending.get("tool"):
        detail = pending.get("detail") or ""
        body = "%s: %s" % (pending["tool"], detail) if detail else pending["tool"]
    elif not body:
        body = meta["title"]
    return title, body, meta["sound"], meta["sticky"]
