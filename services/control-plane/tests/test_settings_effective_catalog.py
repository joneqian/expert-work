"""Stream O Mini-ADR O-10 — legacy → effective credentials-catalog derivation.

The platform-infra callers (embedder / reranker) migrate to
:class:`CredentialsResolver`. A deployment that has NOT opted into Stream O
(empty ``platform_provider_credentials``) but still sets the legacy
``embedding_api_key_ref`` / ``rerank_api_key_ref`` must keep working
transparently — the ``effective_*`` properties gap-fill the catalog from the
legacy fields so platform mode resolves them. (The legacy
``tavily_api_key_ref`` gap-fill was removed in M2 — web_search's builtin
backend is now keyless SearXNG.)
"""

from __future__ import annotations

from control_plane.settings import Settings


def _settings(**overrides: object) -> Settings:
    # Pin every field the ``effective_*`` derivation reads so the OS env
    # cannot pollute these unit assertions.
    base: dict[str, object] = {
        "supported_providers": [],
        "platform_provider_credentials": {},
        "supported_tools": [],
        "platform_tool_credentials": {},
        "embedding_api_key_ref": None,
        "rerank_api_key_ref": None,
        "web_search_searxng_base_url": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_no_credentials_configured_yields_empty_catalog() -> None:
    cfg = _settings()
    assert cfg.effective_platform_provider_credentials == {}
    assert cfg.effective_platform_tool_credentials == {}
    assert cfg.effective_supported_providers == []
    assert cfg.effective_supported_tools == []


def test_legacy_embedding_ref_derives_provider_credential() -> None:
    cfg = _settings(
        embedding_api_key_ref="secret://qwen-embed",
        embedding_provider="qwen",
    )
    assert cfg.effective_platform_provider_credentials == {"qwen": "secret://qwen-embed"}
    assert cfg.effective_supported_providers == ["qwen"]


def test_legacy_rerank_ref_derives_provider_credential() -> None:
    cfg = _settings(
        rerank_api_key_ref="secret://qwen-rerank",
        rerank_provider="qwen",
    )
    assert cfg.effective_platform_provider_credentials == {"qwen": "secret://qwen-rerank"}


def test_legacy_embedding_and_rerank_distinct_providers_both_derived() -> None:
    cfg = _settings(
        embedding_api_key_ref="secret://qwen-embed",
        embedding_provider="qwen",
        rerank_api_key_ref="secret://oai-rerank",
        rerank_provider="openai",
    )
    assert cfg.effective_platform_provider_credentials == {
        "qwen": "secret://qwen-embed",
        "openai": "secret://oai-rerank",
    }
    assert set(cfg.effective_supported_providers) == {"qwen", "openai"}


def test_searxng_base_url_makes_web_search_supported_without_credential() -> None:
    # SearXNG needs no API key, so it cannot ride the tool-credential
    # gap-fill — the URL alone gates web_search as supported.
    cfg = _settings(web_search_searxng_base_url="http://searxng:8080")
    assert cfg.effective_platform_tool_credentials == {}
    assert cfg.effective_supported_tools == ["web_search"]


def test_explicit_tool_credential_still_lists_web_search() -> None:
    # The generic platform_tool_credentials mechanism is unchanged; an
    # explicit web_search secret_ref still marks it supported.
    cfg = _settings(platform_tool_credentials={"web_search": "secret://x"})
    assert cfg.effective_supported_tools == ["web_search"]


def test_explicit_stream_o_credential_wins_over_legacy() -> None:
    # Explicit Stream O config for the same provider must NOT be clobbered
    # by the legacy gap-fill.
    cfg = _settings(
        supported_providers=["qwen"],
        platform_provider_credentials={"qwen": "secret://explicit"},
        embedding_api_key_ref="secret://legacy-embed",
        embedding_provider="qwen",
    )
    assert cfg.effective_platform_provider_credentials == {"qwen": "secret://explicit"}
    assert cfg.effective_supported_providers == ["qwen"]


def test_legacy_gap_fills_only_absent_providers() -> None:
    # Explicit covers openai; legacy embedding on qwen fills the gap.
    cfg = _settings(
        supported_providers=["openai"],
        platform_provider_credentials={"openai": "secret://oai"},
        embedding_api_key_ref="secret://qwen-embed",
        embedding_provider="qwen",
    )
    assert cfg.effective_platform_provider_credentials == {
        "openai": "secret://oai",
        "qwen": "secret://qwen-embed",
    }
    assert set(cfg.effective_supported_providers) == {"openai", "qwen"}


def test_embedding_provider_defaults_to_qwen() -> None:
    assert _settings().embedding_provider == "qwen"
