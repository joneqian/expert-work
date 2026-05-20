"""Stream K.K7 — content normalisation + SHA-256 helper.

Single source of truth for the dedup hash. Kept tiny on purpose: the
migration's PG-side backfill computes ``encode(digest(lower(trim(content)),
'sha256'), 'hex')`` and the Python ``hash_content`` here must produce
exactly the same hex for the same input. If the normalisation rule ever
changes, both sides must change together and the existing rows need a
re-hash migration.
"""

from __future__ import annotations

import hashlib


def normalise_content(content: str) -> str:
    """``str.lower()`` after ``str.strip()`` — the single normalisation
    rule shared by application + DB-side backfill."""
    return content.strip().lower()


def hash_content(content: str) -> str:
    """SHA-256 hex of ``normalise_content(content)`` — 64 chars."""
    digest = hashlib.sha256(normalise_content(content).encode("utf-8"))
    return digest.hexdigest()
