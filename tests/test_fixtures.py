"""Verifies Phase 0.5 test infrastructure fixtures work as advertised."""

from __future__ import annotations

import pytest

from expert_work.testing import FakeCompletion, InMemorySecretStore, MockLLM

# ---------------------------------------------------------------------------
# mock_llm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_llm_default_response(mock_llm: MockLLM) -> None:
    """Without expectations, mock_llm returns its default."""
    result = await mock_llm.complete("anything")
    assert result.content == "ok"
    assert mock_llm.calls == ["anything"]


@pytest.mark.asyncio
async def test_mock_llm_prefix_expectation(mock_llm: MockLLM) -> None:
    """expect(prefix, response) routes matching prompts."""
    mock_llm.expect("summarize:", FakeCompletion(content="<summary>", tokens_used=12))

    result = await mock_llm.complete("summarize: expert-work docs")
    assert result.content == "<summary>"
    assert result.tokens_used == 12

    fallback = await mock_llm.complete("unrelated prompt")
    assert fallback.content == "ok"


@pytest.mark.asyncio
async def test_mock_llm_records_all_calls(mock_llm: MockLLM) -> None:
    """Every prompt is recorded for downstream assertions."""
    await mock_llm.complete("a")
    await mock_llm.complete("b")
    await mock_llm.complete("c")
    assert mock_llm.calls == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# mock_secret_store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_secret_store_put_get(mock_secret_store: InMemorySecretStore) -> None:
    """put() then get() returns the stored value."""
    await mock_secret_store.put("/expert-work/test/db-password", "s3cret")
    value = await mock_secret_store.get("/expert-work/test/db-password")
    assert value == "s3cret"


@pytest.mark.asyncio
async def test_mock_secret_store_missing_raises(mock_secret_store: InMemorySecretStore) -> None:
    """Reading an unset key raises KeyError."""
    with pytest.raises(KeyError, match="secret not found"):
        await mock_secret_store.get("/expert-work/test/missing")


@pytest.mark.asyncio
async def test_mock_secret_store_delete(mock_secret_store: InMemorySecretStore) -> None:
    """delete() removes the value; a subsequent get() raises."""
    await mock_secret_store.put("/expert-work/test/db-password", "s3cret")
    await mock_secret_store.delete("/expert-work/test/db-password")
    with pytest.raises(KeyError, match="secret not found"):
        await mock_secret_store.get("/expert-work/test/db-password")


@pytest.mark.asyncio
async def test_mock_secret_store_delete_missing_is_idempotent(
    mock_secret_store: InMemorySecretStore,
) -> None:
    """delete() on an absent name does NOT raise (idempotent)."""
    await mock_secret_store.delete("/expert-work/test/never-existed")
    with pytest.raises(KeyError, match="secret not found"):
        await mock_secret_store.get("/expert-work/test/never-existed")
