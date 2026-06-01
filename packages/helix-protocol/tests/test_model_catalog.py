"""MODEL_CATALOG shape + lookup — Stream S PR B (Mini-ADR S-4)."""

from helix_agent.protocol import (
    MODEL_CATALOG,
    ModelEntry,
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
