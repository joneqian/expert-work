"""Outbound DLP — PII redaction of model output (Stream PI-2 / 7.4).

Output screening (:mod:`helix_agent.common.output_screen`) *blocks* a response
that leaks a credential / exfil form. DLP is the complementary **conditional
output** tier: it does not block — it **redacts** personal data (email, phone,
national id, payment card) the model emitted before the reply reaches the user
or a downstream tool. This closes the outbound gap left by the PII-redact
middleware, which only masks prompts on the way *into* the model (E.5), never
the model's fresh response.

The patterns mirror ``helix_agent.runtime.audit.redactor.PII_PATTERNS`` in
intent but are kept self-contained here: ``helix-common`` must not depend on
``helix-runtime`` (wrong layer direction). They are heuristics, not NER — names
and free-form addresses are out of scope until a Presidio-class follow-up.

Pure + deterministic (no model, no I/O): unit-tests offline, safe on every
terminal turn. Like :mod:`output_screen`, matched values are NEVER returned or
logged — the result carries only *category names* so callers emit metrics /
audit without re-leaking what the redactor just caught (CodeQL
py/clear-text-logging).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Replacement token emitted in place of a matched PII span. Generic and fixed —
#: it must not echo what was caught.
DLP_REPLACEMENT = "[redacted]"

#: Conversational PII categories → high-precision shapes. Each is rarely a
#: legitimate verbatim model emission to a downstream sink, so redacting is low
#: risk; the manifest defaults this tier OFF so it only runs where an operator
#: opted in (it can alter a legitimate "your email is …" answer).
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Mainland China mobile: 11 digits starting 13x-19x.
    ("phone_cn", re.compile(r"\b1[3-9]\d{9}\b")),
    # Mainland China resident id: 17 digits + checksum (digit or X).
    ("id_card_cn", re.compile(r"\b\d{17}[\dXx]\b")),
    # 16-digit payment card, optionally grouped in 4s by space/hyphen.
    ("credit_card", re.compile(r"\b\d{4}(?:[ -]?\d{4}){3}\b")),
)


@dataclass(frozen=True)
class DlpResult:
    """Outcome of running outbound DLP over one piece of model output.

    ``redacted`` is the text with every matched PII span replaced by
    :data:`DLP_REPLACEMENT`. ``categories`` names *what kinds* fired (e.g.
    ``"email"``, ``"id_card_cn"``) — never the matched value. ``changed`` is
    ``bool(categories)``.
    """

    redacted: str
    categories: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.categories)


def scan_and_redact(text: str) -> DlpResult:
    """Redact PII shapes in ``text``; return the cleaned text + hit categories.

    Each pattern runs over the progressively-redacted text; the categories are
    independent so order does not change the result. The input is never
    mutated. Matched values are dropped — only category names survive.
    """
    out = text
    categories: list[str] = []
    for name, pattern in _PII_PATTERNS:
        new_out, count = pattern.subn(DLP_REPLACEMENT, out)
        if count:
            categories.append(name)
            out = new_out
    return DlpResult(redacted=out, categories=tuple(categories))


__all__ = ["DLP_REPLACEMENT", "DlpResult", "scan_and_redact"]
