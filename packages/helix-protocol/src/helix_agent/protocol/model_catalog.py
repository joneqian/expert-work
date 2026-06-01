"""Per-provider model catalog — Stream S PR B (Mini-ADR S-4).

Drives the visual manifest editor's model dropdown: provider → selectable
models + capability flags. ``vision`` gates whether ``ModelSpec.supports_vision``
may be set; ``embeddings`` marks providers usable for long-term memory.

Kept current by hand (small, single source). When extending, verify the
provider's *current* in-sale model names + vision capability against the
provider's official docs — do NOT carry stale names. Mark retired models
``deprecated=True`` so they stay referenceable but drop out of the dropdown
(``models_for_provider``).

Last verified: 2026-06 against each provider's official API docs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG, Provider


class ModelEntry(BaseModel):
    """One selectable model for a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    vision: bool = False
    embeddings: bool = False
    context_window: int | None = None
    deprecated: bool = False


#: Provider → its models. Verify names/capabilities against official docs when
#: editing (Mini-ADR S-4).
MODEL_CATALOG: dict[Provider, tuple[ModelEntry, ...]] = {
    # Anthropic — docs.anthropic.com/en/docs/about-claude/models/overview (2026-06)
    # IDs use dateless format since 4.6 generation. claude-opus-4-8 is flagship.
    "anthropic": (
        ModelEntry(name="claude-opus-4-8", vision=True, context_window=200_000),
        ModelEntry(name="claude-sonnet-4-6", vision=True, context_window=200_000),
        ModelEntry(name="claude-haiku-4-5", vision=True, context_window=200_000),
    ),
    # OpenAI — platform.openai.com/docs/models (2026-06)
    # gpt-5.4 / gpt-5.4-mini are current in-sale; gpt-4o family retired from
    # ChatGPT on 2026-02-13 but still callable via API — kept as deprecated so
    # existing manifests remain referenceable.
    "openai": (
        ModelEntry(name="gpt-5.4", vision=True, context_window=128_000),
        ModelEntry(name="gpt-5.4-mini", vision=True, context_window=128_000),
        ModelEntry(name="text-embedding-3-large", embeddings=True),
        ModelEntry(name="gpt-4o", vision=True, context_window=128_000, deprecated=True),
        ModelEntry(name="gpt-4o-mini", vision=True, context_window=128_000, deprecated=True),
    ),
    # DeepSeek — api-docs.deepseek.com (2026-06)
    # deepseek-v4-pro / deepseek-v4-flash are current (1M context, dual mode).
    # deepseek-chat / deepseek-reasoner map to deepseek-v4-flash; scheduled for
    # retirement 2026-07-24 — kept non-deprecated until then so manifests still
    # route correctly, but operators should migrate to versioned names.
    "deepseek": (
        ModelEntry(name="deepseek-v4-pro", vision=False, context_window=1_000_000),
        ModelEntry(name="deepseek-v4-flash", vision=False, context_window=1_000_000),
        ModelEntry(name="deepseek-chat", vision=False, context_window=64_000),
        ModelEntry(name="deepseek-reasoner", vision=False, context_window=64_000),
    ),
    # Kimi (Moonshot AI) — platform.moonshot.cn/docs (2026-06)
    # kimi-k2.6 and kimi-k2.5 are current; moonshot-v1 series still callable
    # but being phased out in favour of the K2 lineup.
    "kimi": (
        ModelEntry(name="kimi-k2.6", vision=False, context_window=128_000),
        ModelEntry(name="kimi-k2.5", vision=False, context_window=128_000),
        ModelEntry(name="moonshot-v1-128k", vision=False, context_window=128_000, deprecated=True),
        ModelEntry(name="moonshot-v1-32k", vision=False, context_window=32_000, deprecated=True),
    ),
    # Zhipu GLM — open.bigmodel.cn (2026-06)
    # glm-4-plus (128K, text) and glm-4v-plus (8K, vision) remain available.
    # glm-4.1v-thinking is a newer vision-reasoning model.
    "glm": (
        ModelEntry(name="glm-4-plus", vision=False, context_window=128_000),
        ModelEntry(name="glm-4v-plus", vision=True, context_window=8_000),
        ModelEntry(name="glm-4.1v-thinking", vision=True, context_window=32_000),
    ),
    # Alibaba Qwen / DashScope — alibabacloud.com/help/en/model-studio (2026-06)
    # qwen3-max is the current flagship; qwen3-vl-plus supports vision.
    # Legacy qwen-max / qwen-vl-max still callable but superseded.
    "qwen": (
        ModelEntry(name="qwen3-max", vision=False, context_window=128_000),
        ModelEntry(name="qwen3-vl-plus", vision=True, context_window=128_000),
        ModelEntry(name="qwen-max", vision=False, context_window=32_000, deprecated=True),
        ModelEntry(name="qwen-vl-max", vision=True, context_window=32_000, deprecated=True),
    ),
    # Doubao (ByteDance Volcano Engine) — volcengine.com (2026-06)
    # Seed 2.0 family (released 2026-02-14) is current; all tiers support vision
    # and 256K context. Older doubao-*-32k series superseded.
    "doubao": (
        ModelEntry(name="doubao-seed-2.0-pro", vision=True, context_window=256_000),
        ModelEntry(name="doubao-seed-2.0-lite", vision=True, context_window=256_000),
        ModelEntry(name="doubao-pro-32k", vision=False, context_window=32_000, deprecated=True),
        ModelEntry(
            name="doubao-vision-pro-32k", vision=True, context_window=32_000, deprecated=True
        ),
    ),
}


def models_for_provider(provider: str) -> tuple[ModelEntry, ...]:
    """Non-deprecated models for ``provider`` (empty for unknown providers)."""
    if provider not in PROVIDER_CATALOG:
        return ()
    entries = MODEL_CATALOG.get(provider, ())
    return tuple(e for e in entries if not e.deprecated)
