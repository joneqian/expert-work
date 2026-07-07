"""Ranked natural-language retrieval over deferred tools — Stream HX-12.

BM25 over a small in-memory corpus (one document per deferred tool: the
name split into words + the description + top-level parameter names),
tokenized with jieba so Chinese queries and descriptions segment
correctly (the J.5 keyword-search precedent in expert-work-persistence).

The public surface is :func:`rank_tools` — its signature carries no BM25
concept (Mini-ADR HX-I2: the vector-retrieval seam is a function swap,
not a Protocol). Zero-overlap queries score 0 everywhere and return an
empty list; the caller (``ToolRegistry.search``) falls back to its
substring path so retrieval is never worse than the pre-HX-12 behaviour.
"""

from __future__ import annotations

import re

import jieba
from rank_bm25 import BM25Plus

#: Results per query — Hermes-comparable; more is noise for the model.
TOP_K = 8

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEP_RE = re.compile(r"[_\-./:]+")


def split_identifier(name: str) -> list[str]:
    """Split a tool identifier into searchable words.

    ``mcp:github.create_pull_request`` → ``["mcp", "github", "create",
    "pull", "request"]``; camelCase splits too.
    """
    parts = _SEP_RE.split(_CAMEL_RE.sub(" ", name))
    return [p.lower() for chunk in parts for p in chunk.split() if p]


def tokenize(text: str) -> list[str]:
    """jieba-segmented, lower-cased tokens (CJK-aware; ~pass-through ASCII)."""
    return [t.strip().lower() for t in jieba.cut(text) if t.strip()]


def build_document(name: str, description: str, parameter_names: list[str]) -> list[str]:
    """The token document indexed for one tool."""
    tokens = split_identifier(name)
    tokens.extend(tokenize(description))
    for param in parameter_names:
        tokens.extend(split_identifier(param))
    return tokens


def rank_tools(
    query: str,
    corpus: list[tuple[str, list[str]]],
    *,
    top_k: int = TOP_K,
) -> list[str]:
    """Rank tool names by relevance to a natural-language ``query``.

    ``corpus`` is ``[(tool_name, token_document), ...]`` (see
    :func:`build_document`). Returns up to ``top_k`` names with a strictly
    positive score, best first. An empty result means zero lexical overlap
    — the caller should fall back to substring matching.
    """
    if not corpus:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    # BM25Plus rather than Okapi: on a small corpus (a handful of deferred
    # tools) Okapi's IDF goes to zero/negative for any term appearing in
    # most documents, silently filtering true hits. Plus keeps scores
    # positive; relevance gating is done explicitly below — a document
    # must share at least one token with the query (otherwise Plus's
    # delta smoothing would rank zero-overlap documents too).
    bm25 = BM25Plus([doc for _, doc in corpus])
    scores = bm25.get_scores(query_tokens)
    query_set = set(query_tokens)
    hits = [idx for idx, (_, doc) in enumerate(corpus) if query_set & set(doc)]
    hits.sort(key=lambda idx: (-scores[idx], idx))
    return [corpus[idx][0] for idx in hits[:top_k]]
