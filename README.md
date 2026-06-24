# claude-watch

A tiny, local, Santa-safe replacement for Masko: a **menu bar live feed + desktop
notifications** for your Claude Code sessions.

- 🛰️ menu bar icon shows how many sessions are active; the dropdown lists each
  session (project · status · last few actions).
- Desktop notification when a session **finishes** (✅ "your turn") or **needs
  permission / your attention** (🟡).
- 100% local. No network, no analytics, no phone-home. Everything lives in
  `~/.claude-watch/`.

## Why it can't get Santa-blocked

Masko shipped a compiled `hook-sender` binary, which your company's Santa "Team ID
rule" blocked. claude-watch ships **no binaries**. Both scripts run under the
Apple-signed system interpreter `/usr/bin/python3` (which already bundles PyObjC),
so Santa evaluates the approved interpreter, not a new binary. Notifications go
through the system `/usr/bin/osascript`.

## How it works

```
Claude Code hook ──stdin JSON──▶ hook.py ──append──▶ ~/.claude-watch/events-DATE.ndjson
                                                              │ tail (1s)
                                                              ▼
                                                    bar.py (menu bar app)
                                                       ├─ live feed dropdown
                                                       └─ osascript notifications
```

- **`hook.py`** — registered for the relevant Claude Code hook events. Normalizes
  each payload and appends one NDJSON line. Pure capture; never blocks the agent,
  never writes to stdout, always exits 0.
- **`bar.py`** — `NSStatusItem` menu bar app (PyObjC) that tails today's log,
  tracks per-session state, renders the feed, and fires notifications. Started at
  login by a LaunchAgent.

## Install

```sh
/usr/bin/python3 install.py
```

This backs up `~/.claude/settings.json`, registers the capture hook, installs the
`com.claudewatch.bar` LaunchAgent, and starts the menu bar app. Restart any running
Claude Code sessions so they pick up the new hooks.

## Uninstall

```sh
/usr/bin/python3 uninstall.py          # remove hooks + LaunchAgent, keep data
/usr/bin/python3 uninstall.py --purge  # also delete ~/.claude-watch
```

## Try it without installing

```sh
/usr/bin/python3 bar.py --demo   # replays synthetic events into the feed
```

## Test

```sh
/usr/bin/python3 test_hook.py
```

## Data & files

| Path | What |
|------|------|
| `~/.claude-watch/events-YYYY-MM-DD.ndjson` | captured events (one per line) |
| `~/.claude-watch/config.json` | `{ "muted": bool }` |
| `~/.claude-watch/bar.log` | menu bar app stdout/stderr |
| `~/Library/LaunchAgents/com.claudewatch.bar.plist` | login agent |

## Possible next steps

The NDJSON log already contains everything needed to add **history/search** and
**usage analytics** later without changing the capture layer.
