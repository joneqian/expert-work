"""Recursive ``str`` walker for trigger-config strict scanning.

See ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 2.3 (Mini-ADR U-2 Layer A).

Why recursive: trigger ``config`` is ``dict[str, Any]`` and future
schemas may add prompt-bearing fields. A whitelist would silently fail
when a new field lands; the walker covers them by default. Cost: edge
false-positives (e.g. URLs containing directional marks), which we
accept and resolve via runbook tuning.

Why a size cap: a single ``str`` leaf > ``MAX_FIELD_BYTES`` is rejected
**before** scanning to prevent a degenerate-input DoS on the regex
engine (catastrophic backtracking on attacker-tuned payloads).
``10_240`` bytes is generous — K.K12 baseline P99 prompt length is
~2 KB.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from expert_work.common.threat_patterns import ThreatFinding, scan_for_threats

#: Max bytes a single ``str`` leaf may have before strict scanning rejects it.
MAX_FIELD_BYTES = 10 * 1024


class FieldTooLargeError(ValueError):
    """Raised when a ``str`` leaf exceeds :data:`MAX_FIELD_BYTES`.

    Carries ``path`` (dot-joined keys / indices) for audit context.
    """

    def __init__(self, *, path: str, length: int) -> None:
        super().__init__(
            f"field {path!r} is {length} bytes — exceeds the {MAX_FIELD_BYTES}-byte cap "
            f"for security scanning."
        )
        self.path = path
        self.length = length


def _walk(value: Any, *, path: str) -> Iterator[tuple[str, str]]:
    """Yield ``(path, str_value)`` for every ``str`` leaf in ``value``.

    Non-str scalars (int / float / bool / None) are ignored. Bytes are
    decoded latin-1 lossless and yielded (so we still scan binary).
    """
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, bytes):
        yield path, value.decode("latin-1", errors="replace")
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, path=f"{path}.{k}" if path else str(k))
    elif isinstance(value, list | tuple):
        for i, item in enumerate(value):
            yield from _walk(item, path=f"{path}[{i}]")
    # int / float / bool / None — nothing to scan.


def scan_payload_strict(
    *,
    name: str,
    config: dict[str, Any],
) -> tuple[str, list[ThreatFinding]] | None:
    """Strict-scope scan of ``name`` + every ``str`` leaf in ``config``.

    Returns ``None`` if clean; otherwise ``(field_path, findings)`` for
    the first non-clean field. Stops at the first finding so the audit
    row points at one specific field.

    Raises :class:`FieldTooLargeError` if any ``str`` leaf is over
    :data:`MAX_FIELD_BYTES` — callers should map that to HTTP 422.
    """
    # ``name`` first so it shows up cleanly in audit when it's the cause.
    leaves: list[tuple[str, str]] = [("name", name)]
    leaves.extend(_walk(config, path="config"))

    for path, text in leaves:
        if len(text.encode("utf-8")) > MAX_FIELD_BYTES:
            raise FieldTooLargeError(path=path, length=len(text.encode("utf-8")))
        findings = scan_for_threats(text, scope="strict")
        if findings:
            return path, findings
    return None


__all__ = ["MAX_FIELD_BYTES", "FieldTooLargeError", "scan_payload_strict"]
