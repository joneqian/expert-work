"""Shared LIKE-pattern escaping for case-insensitive substring search.

One source for every ``ILIKE '%q%'`` filter (thread titles, mirrored
message content) so wildcard escaping can't drift between them.
"""

from __future__ import annotations


def like_contains(q: str) -> str:
    """Escape LIKE wildcards so ``q`` matches literally inside a
    ``ilike(..., escape="\\")`` containment pattern."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
