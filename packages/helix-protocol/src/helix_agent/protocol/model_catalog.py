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

from typing import Literal

from pydantic import BaseModel, ConfigDict

from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG, Provider


class ModelEntry(BaseModel):
    """One selectable model for a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    vision: bool = False
    embeddings: bool = False
    # ``rerank`` marks rerank-capable models for the platform rerank config (Stream T).
    rerank: bool = False
    context_window: int | None = None
    deprecated: bool = False
    # Stream CM-9 (Mini-ADR CM-J3) / CM-10 (Mini-ADR CM-L1) — compute-control
    # capability bits. ``thinking`` is the vendor's runtime thinking-depth
    # control shape (vendor params verified 2026-06-10):
    #   "effort" — native multi-level knob (Anthropic ``output_config.effort``;
    #              OpenAI/Azure/DeepSeek ``reasoning_effort``)
    #   "budget" — continuous thinking-token budget (Qwen ``enable_thinking`` +
    #              ``thinking_budget``; Doubao ``thinking.budget_tokens``)
    #   "toggle" — on/off only, no depth (GLM / Kimi K2.5+ ``thinking.type``)
    #   None     — no runtime control (Haiku; always-thinking models like
    #              deepseek-reasoner / kimi-k2-thinking; embeddings)
    # ``sampling`` marks models still accepting ``temperature``/``top_p`` —
    # Anthropic removed sampling params from Opus 4.7+ (sending one is a 400).
    thinking: Literal["effort", "budget", "toggle"] | None = None
    sampling: bool = True


#: Provider → its models. Verify names/capabilities against official docs when
#: editing (Mini-ADR S-4).
MODEL_CATALOG: dict[Provider, tuple[ModelEntry, ...]] = {
    # Anthropic — docs.anthropic.com/en/docs/about-claude/models/overview (2026-06)
    # IDs use dateless format since 4.6 generation. claude-opus-4-8 is flagship.
    "anthropic": (
        # CM-9: opus-4-8 dropped sampling params (4.7+ removal); haiku has
        # no effort support — verified against the Anthropic docs 2026-06.
        ModelEntry(
            name="claude-opus-4-8",
            vision=True,
            context_window=200_000,
            thinking="effort",
            sampling=False,
        ),
        ModelEntry(
            name="claude-sonnet-4-6",
            vision=True,
            context_window=200_000,
            thinking="effort",
        ),
        ModelEntry(name="claude-haiku-4-5", vision=True, context_window=200_000),
    ),
    # OpenAI — platform.openai.com/docs/models (2026-06)
    # GPT-5.5 / GPT-5.5 Pro (2026-04-24) are the current production flagships and
    # support vision; gpt-5.4-mini stays for low-latency/cost. gpt-4o family is
    # retired from the API but kept deprecated so existing manifests resolve.
    "openai": (
        ModelEntry(name="gpt-5.5", vision=True, context_window=128_000, thinking="effort"),
        ModelEntry(name="gpt-5.5-pro", vision=True, context_window=128_000, thinking="effort"),
        ModelEntry(name="gpt-5.4-mini", vision=True, context_window=128_000, thinking="effort"),
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
        ModelEntry(
            name="deepseek-v4-pro", vision=False, context_window=1_000_000, thinking="effort"
        ),
        ModelEntry(
            name="deepseek-v4-flash", vision=False, context_window=1_000_000, thinking="effort"
        ),
        ModelEntry(name="deepseek-chat", vision=False, context_window=64_000),
        ModelEntry(name="deepseek-reasoner", vision=False, context_window=64_000),
    ),
    # Kimi (Moonshot AI) — platform.moonshot.cn/docs (2026-06)
    # kimi-k2.6 (2026-04-20) is natively multimodal — text + image + video via
    # the MoonViT encoder — with a 256K context; k2.5 also accepts images. The
    # moonshot-v1 series is text-only and being phased out (kept deprecated).
    "kimi": (
        ModelEntry(name="kimi-k2.6", vision=True, context_window=256_000, thinking="toggle"),
        ModelEntry(name="kimi-k2.5", vision=True, context_window=128_000, thinking="toggle"),
        ModelEntry(name="moonshot-v1-128k", vision=False, context_window=128_000, deprecated=True),
        ModelEntry(name="moonshot-v1-32k", vision=False, context_window=32_000, deprecated=True),
    ),
    # Zhipu GLM — docs.bigmodel.cn (2026-06)
    # glm-5.1 (2026-04, 200K ctx / 128K out, deep-thinking) is the text flagship;
    # glm-4.7 (355B MoE, 200K) and glm-4.6 (200K) are current text models. Vision
    # goes through glm-4.6v (128K) and glm-4.5v. The older glm-4*-plus line is
    # kept deprecated so existing manifests resolve.
    "glm": (
        ModelEntry(name="glm-5.1", vision=False, context_window=200_000, thinking="toggle"),
        ModelEntry(name="glm-4.7", vision=False, context_window=200_000, thinking="toggle"),
        ModelEntry(name="glm-4.6", vision=False, context_window=200_000, thinking="toggle"),
        ModelEntry(name="glm-4.6v", vision=True, context_window=128_000),
        ModelEntry(name="glm-4.5v", vision=True),
        # Platform embedding model (Stream T, user-specified).
        ModelEntry(name="embedding-3", embeddings=True),
        ModelEntry(name="glm-4-plus", vision=False, context_window=128_000, deprecated=True),
        ModelEntry(name="glm-4v-plus", vision=True, context_window=8_000, deprecated=True),
        ModelEntry(name="glm-4.1v-thinking", vision=True, context_window=32_000, deprecated=True),
    ),
    # Alibaba Qwen / DashScope (Model Studio / 百炼) — help.aliyun.com/zh/model-studio (2026-06)
    # qwen3.7-max (2026 flagship) is text-only with a ~1M context; qwen3.6-plus
    # is multimodal (vision: OCR, object localisation, chart/diagram understanding)
    # also at ~1M. qwen3.5-plus is the prior multimodal tier; qwen3-vl-* are the
    # vision tiers. Context windows left unset where not confirmed against the
    # 百炼 console. Legacy qwen-max / qwen-vl-max kept deprecated.
    "qwen": (
        ModelEntry(name="qwen3.7-max", vision=False, context_window=1_000_000, thinking="budget"),
        ModelEntry(name="qwen3.6-plus", vision=True, context_window=1_000_000, thinking="budget"),
        ModelEntry(name="qwen3.5-plus", vision=True, thinking="budget"),
        ModelEntry(name="qwen3-max", vision=False, thinking="budget"),
        ModelEntry(name="qwen3-vl-plus", vision=True),
        ModelEntry(name="qwen3-vl-flash", vision=True),
        # Platform embedding model (Stream T, user-specified).
        ModelEntry(name="text-embedding-v4", embeddings=True),
        # Platform rerank model (Stream T, user-specified).
        ModelEntry(name="qwen3-vl-rerank", rerank=True),
        ModelEntry(name="qwen-max", vision=False, context_window=32_000, deprecated=True),
        ModelEntry(name="qwen-vl-max", vision=True, context_window=32_000, deprecated=True),
    ),
    # Doubao (ByteDance Volcano Engine) — volcengine.com (2026-06)
    # Seed 2.0 family (released 2026-02-14) is current; all tiers support vision
    # and 256K context. Older doubao-*-32k series superseded.
    "doubao": (
        ModelEntry(
            name="doubao-seed-2.0-pro", vision=True, context_window=256_000, thinking="budget"
        ),
        ModelEntry(
            name="doubao-seed-2.0-lite", vision=True, context_window=256_000, thinking="budget"
        ),
        ModelEntry(name="doubao-pro-32k", vision=False, context_window=32_000, deprecated=True),
        ModelEntry(
            name="doubao-vision-pro-32k", vision=True, context_window=32_000, deprecated=True
        ),
    ),
}


def catalog_entry(provider: str, name: str) -> ModelEntry | None:
    """Exact-name catalog lookup — ``None`` for off-catalog models.

    Stream CM-9 — the agent factory gates compute-control parameters
    (``effort`` / sampling) on these capability bits; an off-catalog
    model (custom gateway / self-hosted) is not gated.
    """
    entries: tuple[ModelEntry, ...] = MODEL_CATALOG.get(provider, ())  # type: ignore[call-overload]
    for entry in entries:
        if entry.name == name:
            return entry
    return None


def models_for_provider(provider: str) -> tuple[ModelEntry, ...]:
    """Non-deprecated models for ``provider`` (empty for unknown providers)."""
    if provider not in PROVIDER_CATALOG:
        return ()
    entries = MODEL_CATALOG.get(provider, ())
    return tuple(e for e in entries if not e.deprecated)
