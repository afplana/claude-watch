#!/usr/bin/env python3
"""Two-phase notification dedup (Claude Code hooks can fire 2-4x per event)."""

import os
import time

LOCK_DIR = os.path.expanduser("~/.claude-watch/locks")
LOCK_TTL = 2.0


def _lock_path(key):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:120]
    return os.path.join(LOCK_DIR, safe + ".lock")


def should_skip_duplicate(key):
    """Return True if this notification key was seen within LOCK_TTL seconds."""
    if not key:
        return False
    path = _lock_path(key)
    now = time.time()
    try:
        if os.path.isfile(path):
            age = now - os.path.getmtime(path)
            if age < LOCK_TTL:
                return True
            os.remove(path)
    except OSError:
        pass
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return False
    except FileExistsError:
        try:
            if now - os.path.getmtime(path) < LOCK_TTL:
                return True
            os.remove(path)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except OSError:
            return True
    except OSError:
        return False
    return False
