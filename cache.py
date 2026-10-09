"""Shared versioned JSON caches under .cache/.

Caches are stored as {version, payload} JSON files; a version mismatch
discards the stale file (the caller refetches). Sensitive data (customer
emails, VAT numbers) must stay inside .cache/, which is gitignored.
"""

import json
import os

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


def load(name, version):
    """Load the payload of a cache; returns {} when the file is
    missing, unreadable, or written by another cache version."""
    cache_file = os.path.join(CACHE_DIR, f"{name}.json")
    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if data.get("version") != version:
        return {}
    return data.get("payload", {})


def save(name, version, payload):
    """Persist a cache payload with its version."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{name}.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"version": version, "payload": payload}, f, indent=2)
