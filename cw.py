#!/usr/bin/env python3
"""claude-watch CLI — history search + usage analytics over the event logs.

Reads ~/.claude-watch/events-YYYY-MM-DD.ndjson and offers:

  cw.py search [--project P] [--tool T] [--event E] [--text S]
               [--session ID] [--since DATE] [--until DATE] [--limit N]
  cw.py stats  [--project P] [--since DATE] [--until DATE]

Pure stdlib, runs under /usr/bin/python3. Read-only; never touches the agent.

Note: hooks capture events, not tokens/cost — so analytics cover activity
(sessions, tools, files, active time), not spend.
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

DATA_DIR = os.path.expanduser("~/.claude-watch")
FILE_RE = re.compile(r"events-(\d{4}-\d{2}-\d{2})\.ndjson$")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


# --------------------------------------------------------------- data loading
def log_files(data_dir, since=None, until=None):
    """Dated event logs in chronological order, filtered to [since, until]."""
    out = []
    for path in glob.glob(os.path.join(data_dir, "events-*.ndjson")):
        m = FILE_RE.search(os.path.basename(path))
        if not m:
            continue  # skip events-demo.ndjson etc.
        day = m.group(1)
        if since and day < since:
            continue
        if until and day > until:
            continue
        out.append((day, path))
    return [p for _, p in sorted(out)]


def iter_events(data_dir=DATA_DIR, since=None, until=None):
    for path in log_files(data_dir, since, until):
        try:
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


# ------------------------------------------------------------- pure functions
def match_event(ev, project=None, tool=None, event=None, text=None,
                session=None, since=None, until=None):
    if project and ev.get("project") != project:
        return False
    if tool and ev.get("tool") != tool:
        return False
    if event and ev.get("event") != event:
        return False
    if session and ev.get("session") != session:
        return False
    if text and text.lower() not in (ev.get("detail", "") or "").lower():
        return False
    day = (ev.get("ts", "")[:10])
    if since and day < since:
        return False
    if until and day > until:
        return False
    return True


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def compute_stats(events):
    """Aggregate a list of event dicts into a summary (pure, testable)."""
    events = list(events)
    sessions = defaultdict(list)        # session_id -> [ts, ...]
    sess_project = {}
    per_day_sessions = defaultdict(set)
    tools = Counter()
    files = Counter()
    per_project_events = Counter()
    prompts = 0
    permissions = 0

    for ev in events:
        sid = ev.get("session") or "(none)"
        ts = ev.get("ts", "")
        day = ts[:10]
        project = ev.get("project", "(unknown)")
        event = ev.get("event")

        sessions[sid].append(ts)
        sess_project.setdefault(sid, project)
        if day:
            per_day_sessions[day].add(sid)
        per_project_events[project] += 1

        if event == "PreToolUse":           # count once per tool call
            tool = ev.get("tool")
            if tool:
                tools[tool] += 1
            if tool in EDIT_TOOLS and ev.get("detail"):
                files[ev["detail"]] += 1
        elif event == "UserPromptSubmit":
            prompts += 1
        elif event == "Notification":
            permissions += 1

    # active time per session = last ts - first ts
    total_active = 0.0
    sess_project_count = Counter()
    for sid, stamps in sessions.items():
        sess_project_count[sess_project[sid]] += 1
        parsed = sorted(p for p in (_parse_ts(t) for t in stamps) if p)
        if len(parsed) >= 2:
            total_active += (parsed[-1] - parsed[0]).total_seconds()

    days = sorted(d for d in per_day_sessions if d)
    return {
        "total_events": len(events),
        "total_sessions": len(sessions),
        "date_range": (days[0], days[-1]) if days else (None, None),
        "sessions_per_day": {d: len(per_day_sessions[d]) for d in days},
        "sessions_per_project": dict(sess_project_count.most_common()),
        "events_per_project": dict(per_project_events.most_common()),
        "tool_frequency": dict(tools.most_common()),
        "top_files": dict(files.most_common(10)),
        "prompts": prompts,
        "permission_requests": permissions,
        "total_active_seconds": total_active,
    }


def human_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %dm" % (h, m)
    if m:
        return "%dm %ds" % (m, s)
    return "%ds" % s


# ---------------------------------------------------------------- subcommands
def cmd_search(args):
    rows = []
    for ev in iter_events(since=args.since, until=args.until):
        if match_event(ev, project=args.project, tool=args.tool, event=args.event,
                       text=args.text, session=args.session):
            rows.append(ev)
    if args.limit:
        rows = rows[-args.limit:]
    if not rows:
        print("no matching events")
        return
    print("%-19s  %-16s  %-14s  %-8s  %s" % ("TIME", "PROJECT", "EVENT", "TOOL", "DETAIL"))
    for ev in rows:
        detail = (ev.get("detail", "") or "").replace("\n", " ")
        if len(detail) > 60:
            detail = detail[:59] + "…"
        print("%-19s  %-16.16s  %-14.14s  %-8.8s  %s" % (
            ev.get("ts", ""), ev.get("project", ""), ev.get("event", ""),
            ev.get("tool", ""), detail))
    print("\n%d event(s)" % len(rows))


def _print_bar_table(title, mapping, width=24):
    print("\n%s" % title)
    if not mapping:
        print("  (none)")
        return
    top = max(mapping.values())
    for key, val in mapping.items():
        bar = "█" * max(1, int(width * val / top)) if top else ""
        print("  %-22.22s %5d  %s" % (key, val, bar))


def cmd_stats(args):
    events = (ev for ev in iter_events(since=args.since, until=args.until)
              if not args.project or ev.get("project") == args.project)
    s = compute_stats(events)
    lo, hi = s["date_range"]
    print("claude-watch — usage analytics")
    if lo:
        print("range: %s → %s" % (lo, hi))
    print("sessions: %d   events: %d   prompts: %d   permission prompts: %d" % (
        s["total_sessions"], s["total_events"], s["prompts"], s["permission_requests"]))
    print("total active time: %s" % human_duration(s["total_active_seconds"]))

    _print_bar_table("sessions per day", s["sessions_per_day"])
    _print_bar_table("sessions per project", s["sessions_per_project"])
    _print_bar_table("tool usage", s["tool_frequency"])
    _print_bar_table("most-touched files", s["top_files"])


def build_parser():
    p = argparse.ArgumentParser(prog="cw", description="claude-watch history + analytics")
    sub = p.add_subparsers(dest="cmd", required=True)

    se = sub.add_parser("search", help="filter the event history")
    se.add_argument("--project")
    se.add_argument("--tool")
    se.add_argument("--event")
    se.add_argument("--text", help="substring match against the detail field")
    se.add_argument("--session")
    se.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    se.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    se.add_argument("--limit", type=int, default=50, help="show last N matches (default 50)")
    se.set_defaults(func=cmd_search)

    st = sub.add_parser("stats", help="usage analytics")
    st.add_argument("--project")
    st.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    st.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    st.set_defaults(func=cmd_stats)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
