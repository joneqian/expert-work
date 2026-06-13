"""PII / secret redaction for ``AuditEntry.details``.

Design: subsystems/17-audit-log § 5.2 + STREAM-D-DESIGN § 2.5.

The redactor Protocol is **async + tenant-aware** (D.2): every call site
passes the entry's ``tenant_id`` so a per-tenant ``pii_fields`` lookup
can happen without threading the id through arbitrary middleware. The
default secret redactor still ignores ``tenant_id`` — only
``TenantAwareRedactor`` consults it.

Two redactors are shipped:

* :class:`DefaultSecretRedactor` — fixed global secret patterns (OpenAI
  keys, JWTs, bcrypt hashes, …). The fallback when no
  ``TenantConfigService`` is wired and the implementation used by
  :class:`TenantAwareRedactor` for the global layer.
* :class:`TenantAwareRedactor` — composes a global redactor with a
  per-tenant key-name mask driven by ``tenant_config.pii_fields``
  (D.2). Mini-ADR D-4: keys are matched by **name** (case-insensitive),
  not by content regex; tenant admins can name fields without learning
  per-locale regex.

Strict-mode rejection (refuse to write when a secret is detected) is
still deferred to a follow-up; the M0 contract remains **mask + emit a
metric hit** so we never block the audit path on the redactor itself.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)

REPLACEMENT = "***REDACTED***"

# Hit-counter key used when ``TenantAwareRedactor`` masks a per-tenant
# pii_field. Kept distinct from the global ``DEFAULT_PATTERNS`` keys so
# operators can alert on PII-pattern hits separately.
PII_FIELD_HIT = "pii_field"

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

# Conversational PII — the "regex 扩展起步" tier of Mini-ADR OBS-L1. Kept
# SEPARATE from DEFAULT_PATTERNS on purpose: only the Langfuse mask runs the
# ``{**DEFAULT_PATTERNS, **PII_PATTERNS}`` union (free-text prompts/completions
# are where conversational PII lives), while the audit path stays secrets-only
# so A.7 behaviour is unchanged. These are heuristics, not NER — names and
# free-form addresses are out of scope until a Presidio-class follow-up.
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # Mainland China mobile: 11 digits starting 13x to 19x.
    "phone_cn": re.compile(r"\b1[3-9]\d{9}\b"),
    # Mainland China resident ID: 17 digits + checksum (digit or X).
    "id_card_cn": re.compile(r"\b\d{17}[\dXx]\b"),
    # 16-digit payment card, optionally grouped in 4s by space/hyphen.
    "credit_card": re.compile(r"\b\d{4}(?:[ -]?\d{4}){3}\b"),
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

    Implementations should treat the call as "fast + bounded": one
    redactor call per audit write, on the hot path. The async signature
    lets :class:`TenantAwareRedactor` consult ``TenantConfigService``
    (60s cached, near-zero overhead in steady state) without forcing
    every redactor to be async-IO-bound — :class:`DefaultSecretRedactor`
    just ignores the ``await`` overhead.
    """

    async def redact(
        self,
        *,
        tenant_id: UUID,
        details: Mapping[str, Any],
    ) -> RedactionResult:
        """Return a redacted copy of ``details`` + per-pattern hit counts."""


# Resolver type for the per-tenant pii_fields lookup. A bare callable
# so :class:`TenantAwareRedactor` lives in ``helix-runtime`` without
# importing ``control_plane.tenancy.TenantConfigService`` (that would
# be a reverse-direction dependency). The control-plane wiring at
# ``build_default_audit_logger`` binds the resolver to its own
# ``TenantConfigService``.
PiiFieldsResolver = Callable[[UUID], Awaitable[Sequence[str]]]


class DefaultSecretRedactor:
    """Mask OpenAI keys, Anthropic PATs, JWTs, bcrypt hashes, PEM headers.

    Walks the ``details`` tree recursively. Strings have all matches replaced
    in place; non-string leaves (numbers, bools, None) pass through unchanged.

    The traversal is **deep**: nested dicts and lists are recursed into, so
    a secret hidden under ``details['request']['headers']['authorization']``
    is still caught.

    ``tenant_id`` is accepted to satisfy the :class:`AuditRedactor`
    Protocol but ignored — global patterns are uniform across tenants.
    """

    def __init__(self, patterns: Mapping[str, re.Pattern[str]] | None = None) -> None:
        self._patterns: dict[str, re.Pattern[str]] = dict(patterns or DEFAULT_PATTERNS)

    async def redact(
        self,
        *,
        tenant_id: UUID,
        details: Mapping[str, Any],
    ) -> RedactionResult:
        del tenant_id  # global patterns don't vary by tenant
        hits: dict[str, int] = {}
        cleaned = self._walk(details, hits)
        # ``_walk`` returns ``Any``; at the top level the input is a Mapping,
        # so the result is always a dict. Cast through ``dict()`` to satisfy
        # the static type without an explicit cast import.
        return RedactionResult(redacted=dict(cleaned), hits=hits)

    def redact_text(self, text: str) -> str:
        """Apply the global secret patterns to a single string.

        The text-level entry point for the E.5 PII middleware — same
        patterns as :meth:`redact`, no recursive tree walk, and the
        per-pattern hit counts are dropped (the middleware's
        ``redact_text`` contract returns only the redacted string).
        """
        hits: dict[str, int] = {}
        return self._redact_str(text, hits)

    def redact_tree(self, data: Any) -> Any:
        """Recursively redact every string leaf in an arbitrary JSON-native tree.

        The structured generalisation of :meth:`redact_text` — Mappings,
        lists and tuples are walked; string leaves are pattern-redacted;
        every other leaf (numbers, bools, ``None``, opaque objects) passes
        through unchanged. Hit counts are dropped to match the Langfuse
        ``mask`` callable contract (``data → data``, Mini-ADR OBS-L1). The
        input is never mutated — a fresh structure is returned.
        """
        return self._walk(data, {})

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


class TenantAwareRedactor:
    """Global secret patterns + per-tenant ``pii_fields`` key-name mask.

    Composition: every audit entry is first run through a
    :class:`DefaultSecretRedactor` (or any :class:`AuditRedactor`) for
    global secrets, then the tenant's ``pii_fields`` are looked up via
    ``pii_fields_resolver`` and any matching key names (case-insensitive)
    in the nested ``details`` tree are masked.

    Per Mini-ADR D-4 the per-tenant layer uses **key-name** matching,
    not content regex: tenant admins name fields (``patient_id_card``,
    ``ssn``) without learning per-locale regex; the global layer is
    where content patterns belong.

    Resolver failures (tenant has no ``tenant_config`` row yet,
    ``TenantConfigService`` down) **never block the audit path** — the
    redactor logs and falls back to global-only. Audit writes are
    higher priority than per-tenant PII enforcement; D.3 retention
    cleanup will eventually reconcile.
    """

    def __init__(
        self,
        *,
        global_redactor: AuditRedactor,
        pii_fields_resolver: PiiFieldsResolver,
    ) -> None:
        self._global = global_redactor
        self._resolver = pii_fields_resolver

    async def redact(
        self,
        *,
        tenant_id: UUID,
        details: Mapping[str, Any],
    ) -> RedactionResult:
        # Step 1: global content patterns.
        result = await self._global.redact(tenant_id=tenant_id, details=details)

        # Step 2: per-tenant key-name masking. Soft-fail on resolver
        # errors so audit writes are never blocked.
        try:
            raw_fields = await self._resolver(tenant_id)
        except Exception:
            logger.exception(
                "tenant_aware_redactor.resolver_failed tenant_id=%s; falling back to global-only",
                tenant_id,
            )
            return result

        targets = frozenset(name.lower() for name in raw_fields)
        if not targets:
            return result

        masked, pii_hits = _mask_pii_keys(result.redacted, targets)
        if pii_hits == 0:
            return result

        combined = dict(result.hits)
        combined[PII_FIELD_HIT] = combined.get(PII_FIELD_HIT, 0) + pii_hits
        return RedactionResult(redacted=masked, hits=combined)


def _mask_pii_keys(node: Any, targets: frozenset[str]) -> tuple[Any, int]:
    """Recursively replace values under keys whose lower-cased name ∈ ``targets``.

    Returns ``(new_tree, hit_count)``. The original ``node`` is never
    mutated. Lists and tuples are walked elementwise; primitives pass
    through.

    Match semantics: only **dict keys** are inspected — list indices,
    tuple positions, and string contents are out of scope here (those
    belong to the global content patterns).
    """
    if isinstance(node, Mapping):
        out: dict[Any, Any] = {}
        hits = 0
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in targets:
                out[key] = REPLACEMENT
                hits += 1
            else:
                sub, sub_hits = _mask_pii_keys(value, targets)
                out[key] = sub
                hits += sub_hits
        return out, hits
    if isinstance(node, list):
        items: list[Any] = []
        hits = 0
        for item in node:
            sub, sub_hits = _mask_pii_keys(item, targets)
            items.append(sub)
            hits += sub_hits
        return items, hits
    if isinstance(node, tuple):
        items_t: list[Any] = []
        hits = 0
        for item in node:
            sub, sub_hits = _mask_pii_keys(item, targets)
            items_t.append(sub)
            hits += sub_hits
        return tuple(items_t), hits
    return node, 0
