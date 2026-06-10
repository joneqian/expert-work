"""MODEL_CATALOG shape + lookup — Stream S PR B (Mini-ADR S-4)."""

from helix_agent.protocol import (
    MODEL_CATALOG,
    ModelEntry,
    catalog_entry,
    models_for_provider,
)
from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG


def test_catalog_keys_are_known_providers() -> None:
    for provider in MODEL_CATALOG:
        assert provider in PROVIDER_CATALOG


def test_entries_are_model_entry_with_required_fields() -> None:
    for entries in MODEL_CATALOG.values():
        for e in entries:
            assert isinstance(e, ModelEntry)
            assert e.name
            assert isinstance(e.vision, bool)
            assert isinstance(e.embeddings, bool)


def test_deepseek_chat_present_and_not_vision() -> None:
    names = {e.name: e for e in models_for_provider("deepseek")}
    assert "deepseek-chat" in names
    assert names["deepseek-chat"].vision is False


def test_models_for_provider_excludes_deprecated() -> None:
    for e in models_for_provider("anthropic"):
        assert e.deprecated is False


def test_models_for_unknown_provider_is_empty() -> None:
    assert models_for_provider("not-a-provider") == ()


def test_required_embedding_and_rerank_models_present() -> None:
    glm = {e.name: e for e in MODEL_CATALOG["glm"]}
    qwen = {e.name: e for e in MODEL_CATALOG["qwen"]}
    assert glm["embedding-3"].embeddings is True
    assert qwen["text-embedding-v4"].embeddings is True
    assert qwen["qwen3-vl-rerank"].rerank is True


def test_model_entry_has_rerank_flag_defaulting_false() -> None:
    e = ModelEntry(name="x")
    assert e.rerank is False


# ---------------------------------------------------------------------------
# CM-9 — compute-control capability bits + catalog_entry lookup
# ---------------------------------------------------------------------------


def test_anthropic_capability_bits() -> None:
    opus = catalog_entry("anthropic", "claude-opus-4-8")
    sonnet = catalog_entry("anthropic", "claude-sonnet-4-6")
    haiku = catalog_entry("anthropic", "claude-haiku-4-5")
    assert opus is not None and opus.thinking == "effort" and not opus.sampling
    assert sonnet is not None and sonnet.thinking == "effort" and sonnet.sampling
    assert haiku is not None and haiku.thinking is None and haiku.sampling


def test_catalog_entry_off_catalog_returns_none() -> None:
    assert catalog_entry("anthropic", "claude-imaginary-9") is None
    assert catalog_entry("nonexistent-provider", "x") is None


def test_cross_vendor_thinking_shapes() -> None:
    """CM-10 (Mini-ADR CM-L1) — thinking capability shapes per vendor."""
    assert catalog_entry("openai", "gpt-5.5").thinking == "effort"  # type: ignore[union-attr]
    assert catalog_entry("deepseek", "deepseek-v4-pro").thinking == "effort"  # type: ignore[union-attr]
    assert catalog_entry("qwen", "qwen3.7-max").thinking == "budget"  # type: ignore[union-attr]
    assert catalog_entry("doubao", "doubao-seed-2.0-pro").thinking == "budget"  # type: ignore[union-attr]
    assert catalog_entry("glm", "glm-5.1").thinking == "toggle"  # type: ignore[union-attr]
    assert catalog_entry("kimi", "kimi-k2.6").thinking == "toggle"  # type: ignore[union-attr]
    # Always-thinking / no-control models stay None.
    assert catalog_entry("deepseek", "deepseek-reasoner").thinking is None  # type: ignore[union-attr]
    assert catalog_entry("qwen", "text-embedding-v4").thinking is None  # type: ignore[union-attr]


def test_thinking_defaults_none() -> None:
    assert ModelEntry(name="x").thinking is None
