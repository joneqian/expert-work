"""Search-term tokenization for the J.5 hybrid-retrieval keyword side.

Postgres' built-in ``tsvector`` configurations do not segment CJK text
(Chinese has no word boundaries — ``simple`` would treat a whole
sentence as one token). We tokenize app-side with jieba — CJK-aware,
and a no-op-ish pass-through on English / mixed text — then store and
query under the ``simple`` config. This keeps keyword search correct
for Chinese without a ``zhparser`` / ``pg_jieba`` Postgres extension
(and the custom database image it would require).
"""

from __future__ import annotations

import jieba


def tokenize_for_search(text: str) -> str:
    """Segment ``text`` into space-joined, lower-cased search tokens.

    The result is fed to Postgres ``to_tsvector('simple', ...)`` at
    write time and ``plainto_tsquery('simple', ...)`` at query time, so
    both sides share one segmentation.
    """
    tokens = (token.strip().lower() for token in jieba.cut(text))
    return " ".join(token for token in tokens if token)
