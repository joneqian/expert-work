"""PII / secret redaction for ``AuditEntry.details``.

Design: subsystems/17-audit-log § 5.2.

M0 ships a fixed set of **global** secret patterns. Per-tenant
``pii_fields`` masking depends on ``tenant_config`` (Stream C) and lands
when that schema exists. Strict-mode rejection (refuse to write when a
secret is detected) is deferred to a follow-up; the M0 contract is:
**mask + emit a metric hit** so we never block the audit path on the
redactor itself.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

REPLACEMENT = "***REDACTED***"

# Patterns are anchored loosely on purpose — secrets pasted into details can
# appear mid-string (``"Authorization: Bearer eyJ..."``) and we still need
# to redact them. Order is irrelevant; we run them all.
DEFAULT_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "anthropic_pat": re.compile(r"aforge_pat_[A-Za-z0-9_]+"),
    # JWT three-segment: base64url . base64url . base64url; header always
    # starts with "eyJ" (JSON ``{`` base64-encoded).
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    "bcrypt": re.compile(r"\$2[ayb]\$[0-9]{2}\$[./A-Za-z0-9]{53}"),
    "pem_private_key": re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
}


@dataclass(frozen=True)
class RedactionResult:
    """Outcome of running the redactor over one ``details`` mapping.

    ``redacted`` is a fresh dict — the input is never mutated. ``hits`` maps
    pattern name → match count for observability
    (``helix_audit_redact_hit_total{pattern}`` per subsystems/17 § 7.1).
    """

    redacted: dict[str, Any]
    hits: dict[str, int] = field(default_factory=dict)


class AuditRedactor(Protocol):
    """Strategy interface — call sites depend on this, not the default impl.

    Implementations must be **pure**: same input → same output, no I/O.
    """

    def redact(self, details: Mapping[str, Any]) -> RedactionResult:
        """Return a redacted copy of ``details`` plus per-pattern hit counts."""


class DefaultSecretRedactor:
    """Mask OpenAI keys, Anthropic PATs, JWTs, bcrypt hashes, PEM headers.

    Walks the ``details`` tree recursively. Strings have all matches replaced
    in place; non-string leaves (numbers, bools, None) pass through unchanged.

    The traversal is **deep**: nested dicts and lists are recursed into, so
    a secret hidden under ``details['request']['headers']['authorization']``
    is still caught.
    """

    def __init__(self, patterns: Mapping[str, re.Pattern[str]] | None = None) -> None:
        self._patterns: dict[str, re.Pattern[str]] = dict(patterns or DEFAULT_PATTERNS)

    def redact(self, details: Mapping[str, Any]) -> RedactionResult:
        hits: dict[str, int] = {}
        cleaned = self._walk(details, hits)
        # ``_walk`` returns ``Any``; at the top level the input is a Mapping,
        # so the result is always a dict. Cast through ``dict()`` to satisfy
        # the static type without an explicit cast import.
        return RedactionResult(redacted=dict(cleaned), hits=hits)

    def _walk(self, node: Any, hits: dict[str, int]) -> Any:
        if isinstance(node, Mapping):
            return {k: self._walk(v, hits) for k, v in node.items()}
        if isinstance(node, list):
            return [self._walk(item, hits) for item in node]
        if isinstance(node, tuple):
            # Preserve tuple shape for JSON-incompatible callers; details
            # almost always come from JSON but be defensive.
            return tuple(self._walk(item, hits) for item in node)
        if isinstance(node, str):
            return self._redact_str(node, hits)
        return node

    def _redact_str(self, value: str, hits: dict[str, int]) -> str:
        out = value
        for name, pattern in self._patterns.items():
            new_out, count = pattern.subn(REPLACEMENT, out)
            if count:
                hits[name] = hits.get(name, 0) + count
                out = new_out
        return out
