"""Output screening — prompt-injection backstop (Stream PI-2).

Spotlighting (PI-1/1b) wraps the *untrusted channels* (tool results, retrieved
memory/RAG) so the model can tell data from instructions. It cannot wrap an
injection carried **inline in the user's own message** — that text is, by
definition, trusted-channel input. Output screening is the backstop: scan the
model's *response* right before it reaches the user (or a downstream tool) and
**block** it if it carries a secret/credential leak or a known data-exfil form.

This is the OWASP LLM01:2025 "output screening" layer. PI-2 ships the
deterministic, rule-based tier (high-precision credential + exfil-URL patterns)
so it runs with **no model key** and unit-tests without an LLM. A model-backed
judge tier (for arbitrary-canary / policy leakage the rules can't shape-match)
is deferred — see the design doc, PI-3.

Security note: matched values are NEVER returned or logged. The verdict carries
only *category names* so callers can emit metrics/audit without re-leaking the
secret the screen just caught (CodeQL py/clear-text-logging).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

#: Fixed replacement emitted in place of a blocked response. Deliberately
#: generic — it must not echo what was caught.
REFUSAL_TEXT = (
    "[response withheld] The generated reply tripped an output-safety check "
    "(possible credential leak or data-exfiltration attempt) and was blocked."
)

# --- Rule A: credential / secret shapes ------------------------------------
# Each pattern is an unambiguous credential *shape* — strings of these forms
# are virtually never legitimate model output, so matching them is high
# precision and safe to keep on by default.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # PEM private key
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),  # OpenAI / Anthropic
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[posru]_[A-Za-z0-9]{20,}\b"),  # GitHub token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API key
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),  # HuggingFace token
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),  # GitLab PAT
)

# --- Rule B: data-exfiltration via auto-loading markdown image -------------
# The canonical indirect-injection exfil vector: a markdown *image* whose URL
# carries the stolen data in its query string. The client auto-fetches it on
# render, so the data leaves even if the user never clicks. We require an
# external http(s) host AND a query value that is a long opaque blob — narrow
# enough that ordinary image links (no 24-char query token) don't trip it.
# Plain links / bare URLs are intentionally NOT flagged here (lower precision);
# that belongs to the judge tier (PI-3).
_EXFIL_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*https?://[^)\s]*\?[^)\s]*[A-Za-z0-9+/=_-]{24,}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OutputVerdict:
    """Result of screening one piece of model output.

    ``categories`` names *what kind* of violation fired (e.g. ``"secret"``,
    ``"exfil_url"``, ``"canary"``) — never the matched text. ``blocked`` is
    simply ``bool(categories)``.
    """

    blocked: bool
    categories: tuple[str, ...]


def screen_output(text: str, *, canaries: Sequence[str] = ()) -> OutputVerdict:
    """Screen ``text`` for credential leaks / exfil forms / known canaries.

    Pure + deterministic (no model, no I/O), so it unit-tests offline and is
    safe to run on every model turn. ``canaries`` is an optional set of secret
    values the agent must never emit verbatim (the caller supplies them; empty
    by default — the shape rules still apply).

    Returns an :class:`OutputVerdict`; matched values are not retained.
    """
    categories: list[str] = []

    if any(p.search(text) for p in _SECRET_PATTERNS):
        categories.append("secret")

    if _EXFIL_IMAGE.search(text):
        categories.append("exfil_url")

    if any(c and c in text for c in canaries):
        categories.append("canary")

    return OutputVerdict(blocked=bool(categories), categories=tuple(categories))


__all__ = ["REFUSAL_TEXT", "OutputVerdict", "screen_output"]
