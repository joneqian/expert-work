"""Cursor encoding for ``AuditPage.next_cursor`` / ``AuditQuery.cursor``.

M0 uses the row's ``id`` as the keyset. ``id`` is a BigSerial auto-increment
column on ``audit_log`` (subsystems/17 § 3.1) — strictly monotonic per
Postgres backend, and within a single tenant aligns with ``occurred_at``
order in steady-state. Concurrent writers may briefly invert order, but
M0 query traffic is admin-only and tolerates this.

M2 partitioning + hash chain (subsystems/17 § 5.4) may switch to a
composite (occurred_at, id) cursor; bumping the format here is fine —
cursors are opaque base64 strings to clients.
"""

from __future__ import annotations

import base64
import binascii

_CURSOR_PREFIX = "v1:"


def encode_cursor(audit_id: int) -> str:
    """Encode ``id`` into an opaque cursor string."""
    raw = f"{_CURSOR_PREFIX}{audit_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    """Decode a cursor back to its ``id``.

    :raises ValueError: malformed or unknown-format cursor.
    """
    # Restore base64 padding (we strip it in encode for URL-friendliness).
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded).decode("ascii")
    except (binascii.Error, UnicodeDecodeError) as exc:
        msg = f"malformed cursor: {cursor!r}"
        raise ValueError(msg) from exc

    if not raw.startswith(_CURSOR_PREFIX):
        msg = f"unknown cursor format: {cursor!r}"
        raise ValueError(msg)

    try:
        return int(raw[len(_CURSOR_PREFIX) :])
    except ValueError as exc:
        msg = f"cursor payload is not an integer: {cursor!r}"
        raise ValueError(msg) from exc
