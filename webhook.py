#!/usr/bin/env python3
"""Optional webhook delivery for claude-watch (stdlib urllib only).

Disabled by default — enable in ~/.claude-watch/config.json:

  "webhook": {
    "enabled": true,
    "preset": "slack",
    "url": "https://hooks.slack.com/services/...",
    "chat_id": "123456789"
  }

Presets: slack, discord, telegram, ntfy, teams, custom
"""

import json
import urllib.error
import urllib.request


def _build_payload(preset, title, body, status, wh):
    text = "%s\n%s" % (title, body) if body else title
    preset = (preset or "custom").lower()
    if preset == "slack":
        return json.dumps({"text": text}).encode("utf-8"), "application/json", {}
    if preset == "discord":
        return json.dumps({"content": text}).encode("utf-8"), "application/json", {}
    if preset == "telegram":
        payload = {"chat_id": wh.get("chat_id", ""), "text": text, "parse_mode": "HTML"}
        return json.dumps(payload).encode("utf-8"), "application/json", {}
    if preset == "ntfy":
        headers = {"Title": title[:256], "Priority": "default", "Tags": "claude-watch"}
        return text.encode("utf-8"), "text/plain; charset=utf-8", headers
    if preset == "teams":
        payload = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "themeColor": "0078D7",
            "sections": [{"activityTitle": title, "text": body or status}],
        }
        return json.dumps(payload).encode("utf-8"), "application/json", {}
    return json.dumps({
        "status": status, "title": title, "body": body, "source": "claude-watch",
    }).encode("utf-8"), "application/json", {}


def send_webhook(config, status, title, body):
    """POST a notification to a configured webhook. Best-effort, never raises."""
    wh = (config or {}).get("webhook") or {}
    if not wh.get("enabled") or not wh.get("url"):
        return False
    url = wh["url"]
    preset = wh.get("preset") or "custom"
    data, content_type, extra_headers = _build_payload(preset, title, body, status, wh)
    headers = {"Content-Type": content_type, "User-Agent": "claude-watch/1.0"}
    headers.update(wh.get("headers") or {})
    headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
