"""Unit tests for the SecretStore abstraction — Stream F.6 / ADR-0007."""

from __future__ import annotations

from pathlib import Path

import pytest

from expert_work.runtime.secret_store import (
    InvalidSecretRefError,
    LocalDevSecretStore,
    SecretNotFoundError,
    SecretStore,
    is_secret_ref,
    make_secret_store,
    parse_secret_ref,
)

# ---------------------------------------------------------------------------
# Secret-ref parsing
# ---------------------------------------------------------------------------


def test_parse_secret_ref_canonical_scheme() -> None:
    name = parse_secret_ref("secret://expert-work/dev/llm/anthropic-api-key")
    assert name == "expert-work/dev/llm/anthropic-api-key"


def test_parse_secret_ref_accepts_legacy_kms_alias() -> None:
    """Stream C config used ``kms://`` — tolerated, same resolution."""
    assert parse_secret_ref("kms://dev/llm/key") == "dev/llm/key"


def test_parse_secret_ref_strips_surrounding_slashes() -> None:
    assert parse_secret_ref("secret:///dev/llm/key/") == "dev/llm/key"


def test_parse_secret_ref_rejects_empty_name() -> None:
    with pytest.raises(InvalidSecretRefError, match="empty name"):
        parse_secret_ref("secret://")


def test_parse_secret_ref_rejects_unknown_scheme() -> None:
    with pytest.raises(InvalidSecretRefError, match="unrecognised"):
        parse_secret_ref("vault://dev/llm/key")
    with pytest.raises(InvalidSecretRefError, match="unrecognised"):
        parse_secret_ref("plain-string-not-a-ref")


def test_is_secret_ref() -> None:
    assert is_secret_ref("secret://x") is True
    assert is_secret_ref("kms://x") is True
    assert is_secret_ref("sk-literal-key") is False
    assert is_secret_ref("") is False


# ---------------------------------------------------------------------------
# LocalDevSecretStore — get / put / list_versions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_value() -> None:
    store = LocalDevSecretStore.from_mapping({"a/b/c": "secret-value"})
    assert await store.get("a/b/c") == "secret-value"


@pytest.mark.asyncio
async def test_get_missing_raises_secret_not_found() -> None:
    store = LocalDevSecretStore()
    with pytest.raises(SecretNotFoundError) as exc_info:
        await store.get("nope")
    assert exc_info.value.name == "nope"


@pytest.mark.asyncio
async def test_secret_not_found_is_a_key_error() -> None:
    """Catchable as the explicit type *and* as ``KeyError``."""
    store = LocalDevSecretStore()
    with pytest.raises(KeyError):
        await store.get("nope")


@pytest.mark.asyncio
async def test_put_then_get_round_trip() -> None:
    store = LocalDevSecretStore()
    await store.put("dev/key", "v1")
    assert await store.get("dev/key") == "v1"
    await store.put("dev/key", "v2")
    assert await store.get("dev/key") == "v2"


@pytest.mark.asyncio
async def test_list_versions_returns_synthetic_id() -> None:
    store = LocalDevSecretStore.from_mapping({"k": "v"})
    assert await store.list_versions("k") == ["local-dev"]


@pytest.mark.asyncio
async def test_list_versions_missing_raises() -> None:
    store = LocalDevSecretStore()
    with pytest.raises(SecretNotFoundError):
        await store.list_versions("nope")


@pytest.mark.asyncio
async def test_get_explicit_dev_version_ok_other_version_raises() -> None:
    store = LocalDevSecretStore.from_mapping({"k": "v"})
    assert await store.get("k", version="local-dev") == "v"
    with pytest.raises(SecretNotFoundError):
        await store.get("k", version="v99")


# ---------------------------------------------------------------------------
# LocalDevSecretStore.from_env_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_env_file_parses_entries(tmp_path: Path) -> None:
    env = tmp_path / "secrets.env"
    env.write_text(
        "# a comment\n"
        "\n"
        "expert-work/dev/llm/anthropic-api-key=sk-ant-xxx\n"
        'expert-work/dev/llm/openai-api-key="sk-openai-yyy"\n'
        "quoted/single='single-quoted'\n",
        encoding="utf-8",
    )
    store = LocalDevSecretStore.from_env_file(env)

    assert await store.get("expert-work/dev/llm/anthropic-api-key") == "sk-ant-xxx"
    # Surrounding quotes are stripped.
    assert await store.get("expert-work/dev/llm/openai-api-key") == "sk-openai-yyy"
    assert await store.get("quoted/single") == "single-quoted"


def test_from_env_file_missing_file_yields_empty_store(tmp_path: Path) -> None:
    """A dev checkout without a local .env still boots."""
    store = LocalDevSecretStore.from_env_file(tmp_path / "does-not-exist.env")
    assert store.secrets == {}


@pytest.mark.asyncio
async def test_from_env_file_skips_malformed_lines(tmp_path: Path) -> None:
    env = tmp_path / "s.env"
    env.write_text(
        "# comment\nno-equals-sign-here\nvalid/key=ok\n   \n",
        encoding="utf-8",
    )
    store = LocalDevSecretStore.from_env_file(env)
    assert store.secrets == {"valid/key": "ok"}


# ---------------------------------------------------------------------------
# make_secret_store factory
# ---------------------------------------------------------------------------


def test_make_secret_store_local_dev_default() -> None:
    store = make_secret_store()
    assert isinstance(store, LocalDevSecretStore)


def test_make_secret_store_local_dev_with_env_file(tmp_path: Path) -> None:
    env = tmp_path / "s.env"
    env.write_text("k=v\n", encoding="utf-8")
    store = make_secret_store("local_dev", env_file=env)
    assert isinstance(store, LocalDevSecretStore)
    assert store.secrets == {"k": "v"}


def test_make_secret_store_aliyun_kms_not_implemented() -> None:
    """The production backend is a documented follow-up."""
    with pytest.raises(NotImplementedError, match="aliyun_kms"):
        make_secret_store("aliyun_kms")


def test_make_secret_store_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="unknown secret_store backend"):
        make_secret_store("vault")


# ---------------------------------------------------------------------------
# Protocol contract
# ---------------------------------------------------------------------------


def test_local_dev_store_satisfies_protocol() -> None:
    assert isinstance(LocalDevSecretStore(), SecretStore)
