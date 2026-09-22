#!/usr/bin/env python3
"""Friendly session nicknames (deterministic from session UUID)."""

ADJECTIVES = (
    "bold", "brave", "bright", "calm", "clever", "cool", "cosmic", "crisp", "daring", "eager",
    "fair", "fancy", "fast", "gentle", "glad", "grand", "happy", "kind", "lively", "lucky",
    "merry", "noble", "proud", "quick", "quiet", "rapid", "smart", "solid", "swift", "warm",
    "wise", "witty", "zesty", "agile", "alert",
)
NOUNS = (
    "bear", "bird", "cat", "deer", "eagle", "fish", "fox", "hawk", "lion", "owl",
    "star", "moon", "sun", "wind", "wave", "tree", "river", "mountain", "ocean", "cloud",
    "tiger", "wolf", "dragon", "phoenix", "falcon", "comet", "galaxy", "planet", "nova", "meteor",
)


def session_nickname(session_id):
    """Return a deterministic friendly name like 'cat' from a session UUID."""
    if not session_id or session_id in ("(none)", "unknown"):
        return ""
    clean = session_id.lower().replace("-", "")
    if len(clean) < 8:
        return ""
    seed = clean[:8]
    words = ADJECTIVES + NOUNS
    try:
        index = int(seed[:6], 16) % len(words)
    except ValueError:
        return ""
    return words[index]
