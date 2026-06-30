# claude-watch

A tiny, local, Santa-safe replacement for Masko: a **menu bar live feed + pop-up
alerts** for your Claude Code sessions.

- 🛰️ menu bar icon shows how many sessions are active; the dropdown lists each
  session (project · status · last few actions). A session stops counting as
  active after 10 min idle, so closed CLI instances don't linger.
- A **floating banner** (top-right, auto-dismiss after 8s) when a session
  **finishes** (✅ "your turn") or **needs permission** (🟡). Permission alerts
  include the actual pending command — e.g. `🟡 api-service — approve?` /
  `Bash: rm -rf build/` — correlated from the `PreToolUse` that triggered the
  prompt, since Claude's own message is generic ("Claude needs your permission").
- **Click an alert to jump to its terminal.** The hook records the session's
  `TERM_PROGRAM`, so clicking a banner (or "Focus …" in a session's submenu)
  raises that terminal app — handy when juggling several Claude instances.
  (Best-effort: it raises the app, not the specific tab.)
- **Per-project mute.** Each session's submenu has "Mute this project" to silence
  just the noisy repo while keeping alerts for the one you care about; a global
  mute is still there too. Muted projects show a 🔇 in the dropdown.
- 100% local. No network, no analytics, no phone-home. Everything lives in
  `~/.claude-watch/`.

> **Why a custom banner instead of a real notification?** macOS system
> notifications (`osascript display notification`) proved unreliable on recent
> macOS — they're silently dropped or routed to Notification Center without a
> banner, depending on hidden per-app settings. Since `bar.py` is already a full
> native GUI app (that's how it draws the menu bar icon), it draws its own banner
> window with AppKit — which can't be suppressed by notification settings and
> needs no permissions. Run `/usr/bin/python3 bar.py --banner-test` to see one.

## Why it can't get Santa-blocked

Masko shipped a compiled `hook-sender` binary, which a corporate Santa "Team ID
rule" blocked. claude-watch ships **no binaries**. Both scripts run under the
Apple-signed system interpreter `/usr/bin/python3` (which already bundles PyObjC),
so Santa evaluates the approved interpreter, not a new binary. Alerts are drawn
as native AppKit windows — no external helpers.

## How it works

```
Claude Code hook ──stdin JSON──▶ hook.py ──append──▶ ~/.claude-watch/events-DATE.ndjson
                                                              │ tail (1s)
                                                              ▼
                                                    bar.py (menu bar app)
                                                       ├─ live feed dropdown
                                                       └─ native floating banners
```

- **`hook.py`** — registered for the relevant Claude Code hook events. Normalizes
  each payload and appends one NDJSON line. Pure capture; never blocks the agent,
  never writes to stdout, always exits 0.
- **`bar.py`** — `NSStatusItem` menu bar app (PyObjC) that tails today's log,
  tracks per-session state, renders the feed, and draws floating banner windows
  for finish/permission events. Started at login by a LaunchAgent.

## Install

### Homebrew (recommended)

```sh
brew tap afplana/claude-watch https://github.com/afplana/claude-watch
brew install claude-watch
claude-watch install     # one-time: register hooks + start the menu bar app
```

`brew install` only drops the scripts on disk; `claude-watch install` does the
actual wiring (hooks + LaunchAgent), which is why it's a separate step. The
formula installs no compiled binary — the `claude-watch` command is a shell shim
that runs the scripts under `/usr/bin/python3`, so it stays Santa-safe.

### Manual (clone)

```sh
/usr/bin/python3 install.py
```

Either way this backs up `~/.claude/settings.json`, registers the capture hook,
installs the `com.claudewatch.bar` LaunchAgent, and starts the menu bar app.
Restart any running Claude Code sessions so they pick up the new hooks.

The `claude-watch` command also wraps the rest: `start` / `stop` / `restart` /
`status` the menu bar app, `run [--demo]`, `stats`, `search`, and `uninstall`.

## Uninstall

```sh
/usr/bin/python3 uninstall.py          # remove hooks + LaunchAgent, keep data
/usr/bin/python3 uninstall.py --purge  # also delete ~/.claude-watch
```

## Try it without installing

```sh
/usr/bin/python3 bar.py --demo   # replays synthetic events into the feed
```

## History & analytics CLI

`cw.py` reads the event logs for searching and usage stats (read-only, pure stdlib):

```sh
/usr/bin/python3 cw.py stats                      # sessions/day, tool usage, top files, active time
/usr/bin/python3 cw.py stats --project web-app --since 2026-06-01
/usr/bin/python3 cw.py search --tool Bash --text mvn --limit 20
/usr/bin/python3 cw.py search --event Stop --since 2026-06-20
```

`search` filters: `--project --tool --event --text --session --since --until --limit`.
Handy alias: `alias cw='/usr/bin/python3 ~/stash/claude-watch/cw.py'`.

Note: hooks capture *events*, not tokens/cost, so analytics cover activity
(sessions, tools, files, active time) — not spend.

## Test

```sh
/usr/bin/python3 test_hook.py
/usr/bin/python3 test_cw.py
```

## Data & files

| Path | What |
|------|------|
| `~/.claude-watch/events-YYYY-MM-DD.ndjson` | captured events (one per line) |
| `~/.claude-watch/config.json` | `{ "muted": bool }` |
| `~/.claude-watch/bar.log` | menu bar app stdout/stderr |
| `~/Library/LaunchAgents/com.claudewatch.bar.plist` | login agent |

## Possible next steps

A local web dashboard (stdlib `http.server`) could layer a browsable, charted
view on top of the same `cw.py` query functions.
