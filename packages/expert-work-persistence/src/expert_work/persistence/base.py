# ============================================================
# Partially adapted from bytedance/deer-flow @ 813d3c94efa7fdea6aafcb4f459304db91fcaed0
# Source: backend/packages/harness/deerflow/persistence/base.py
# License: MIT (see vendor LICENSE)
# Modifications:
#   - to_dict() / __repr__() helpers vendored verbatim
#   - No other DeerFlow behaviour pulled in
# Last sync: 2026-05-11
# ============================================================

"""SQLAlchemy DeclarativeBase for Expert Work persistence layer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for ORM models.

    Includes the same ``to_dict()`` / ``__repr__()`` helpers as DeerFlow's
    Base — saves us writing per-model serializers.
    """

    def to_dict(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        """Return ``{column_key: value}`` for every mapped column."""
        skip = exclude or set()
        return {
            col.key: getattr(self, col.key)
            for col in sa_inspect(type(self)).mapper.column_attrs
            if col.key not in skip
        }

    def __repr__(self) -> str:
        cols = ", ".join(
            f"{col.key}={getattr(self, col.key)!r}"
            for col in sa_inspect(type(self)).mapper.column_attrs
        )
        return f"{type(self).__name__}({cols})"
