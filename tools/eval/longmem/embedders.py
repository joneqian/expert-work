"""Embedder construction for runner modes — Stream CM-N5.

``KeywordEmbedder`` is the deterministic no-network arm (same blake2b
keyword-bucket mechanism as ``run_baseline._FakeKeywordEmbedder`` — a
local copy by the same precedent that keeps run_baseline independent of
test code). Real runs build the orchestrator's OpenAI-compatible
embedder from ``HELIX_EVAL_EMBED_*`` env, mirroring how the platform
itself talks to the embedding endpoint — so baseline numbers measure
the production embedding space.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from uuid import UUID


class KeywordEmbedder:
    """Deterministic keyword-overlap embedder (CJK bigrams + ASCII words)."""

    DIM = 256

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        del tenant_id  # eval double has no per-tenant key
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.DIM
        for token in _tokenise(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
            vec[int.from_bytes(digest, "big") % self.DIM] += 1.0
        return tuple(vec)


def _tokenise(text: str) -> list[str]:
    cleaned = text.lower().strip()
    if not cleaned:
        return []
    ascii_words = [w for w in cleaned.replace(",", " ").split() if w]
    cjk_chars = [c for c in cleaned if "一" <= c <= "鿿"]
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return ascii_words + cjk_chars + cjk_bigrams


_EMBED_ENV = ("HELIX_EVAL_EMBED_API_KEY", "HELIX_EVAL_EMBED_MODEL")


def build_real_embedder() -> object:
    """OpenAI-compatible embedder from ``HELIX_EVAL_EMBED_*`` env.

    Required: ``HELIX_EVAL_EMBED_API_KEY`` + ``HELIX_EVAL_EMBED_MODEL``;
    optional ``HELIX_EVAL_EMBED_BASE_URL`` (defaults to the orchestrator
    default endpoint). Imported lazily so the fake arm never pays the
    orchestrator import.
    """
    missing = [name for name in _EMBED_ENV if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"--embedder real requires env {', '.join(missing)} "
            "(plus optional HELIX_EVAL_EMBED_BASE_URL)"
        )
    from orchestrator.llm.embedder import HTTPEmbeddingClient, OpenAICompatibleEmbedder

    base_url = os.environ.get("HELIX_EVAL_EMBED_BASE_URL")
    client = (
        HTTPEmbeddingClient(api_key=os.environ["HELIX_EVAL_EMBED_API_KEY"], base_url=base_url)
        if base_url
        else HTTPEmbeddingClient(api_key=os.environ["HELIX_EVAL_EMBED_API_KEY"])
    )
    return OpenAICompatibleEmbedder(client=client, model=os.environ["HELIX_EVAL_EMBED_MODEL"])
