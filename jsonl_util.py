#!/usr/bin/env python3
"""Parse Claude Code transcript JSONL files (stdlib only).

Ported from claude-notifications-go/pkg/jsonl for local status analysis.
"""

import json
from datetime import datetime


def parse_lines(lines):
    """Parse JSONL text lines into message dicts; skip malformed lines."""
    messages = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def parse_file(path):
    """Read and parse a transcript file; returns [] on error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return parse_lines(fh)
    except OSError:
        return []


def _content_blocks(msg):
    """Yield content blocks from an assistant/user message."""
    message = msg.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        if content:
            yield {"type": "text", "text": content}
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block


def get_last_user_timestamp(messages):
    for msg in reversed(messages):
        if msg.get("type") != "user":
            continue
        message = msg.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return msg.get("timestamp", "")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                return msg.get("timestamp", "")
    return ""


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_messages_after_timestamp(messages, after_ts):
    after = _parse_ts(after_ts)
    if after is None:
        return [m for m in messages if m.get("type") == "assistant"]
    out = []
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        t = _parse_ts(msg.get("timestamp", ""))
        if t is not None and t > after:
            out.append(msg)
    return out


def extract_tools(messages):
    tools = []
    for pos, msg in enumerate(messages):
        for block in _content_blocks(msg):
            if block.get("type") == "tool_use":
                tools.append({"position": pos, "name": block.get("name", "")})
    return tools


def get_last_tool(tools):
    return tools[-1]["name"] if tools else ""


def find_tool_position(tools, name):
    pos = -1
    for tool in tools:
        if tool["name"] == name:
            pos = tool["position"]
    return pos


def count_tools_after_position(tools, position):
    return sum(1 for t in tools if t["position"] > position)


def count_tools_by_names(tools, names):
    names = set(names)
    return sum(1 for t in tools if t["name"] in names)


def has_any_tool(tools, names):
    names = set(names)
    return any(t["name"] in names for t in tools)


def extract_text_from_messages(messages):
    texts = []
    for msg in messages:
        for block in _content_blocks(msg):
            if block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    return texts


def get_last_assistant_messages(messages, count):
    assistant = [m for m in messages if m.get("type") == "assistant"]
    return assistant[-count:] if len(assistant) > count else assistant


def extract_recent_text(messages, count):
    return " ".join(extract_text_from_messages(get_last_assistant_messages(messages, count)))


def has_recent_api_error(messages):
    last_user = get_last_user_timestamp(messages)
    last_user_t = _parse_ts(last_user)
    for msg in reversed(messages):
        if not msg.get("isApiErrorMessage"):
            continue
        if last_user_t is None:
            return True
        t = _parse_ts(msg.get("timestamp", ""))
        if t is not None and t > last_user_t:
            return True
    return False


def get_last_api_error_messages(messages, count):
    errors = [m for m in messages if m.get("isApiErrorMessage")]
    return errors[-count:] if len(errors) > count else errors
