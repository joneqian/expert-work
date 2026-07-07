"""Unit tests for ``make_object_store`` (without hitting a real S3 endpoint)."""

from __future__ import annotations

import pytest

from expert_work.runtime.storage import (
    InMemoryObjectStore,
    S3CompatibleConfig,
    make_object_store,
)


@pytest.mark.asyncio
async def test_memory_backend_yields_in_memory_store() -> None:
    async with make_object_store("memory") as store:
        assert isinstance(store, InMemoryObjectStore)


@pytest.mark.asyncio
async def test_s3_backend_requires_config() -> None:
    with pytest.raises(ValueError, match="requires an S3CompatibleConfig"):
        async with make_object_store("s3-compatible"):
            pass


@pytest.mark.asyncio
async def test_unknown_backend_rejected() -> None:
    with pytest.raises(ValueError, match="unknown object_store backend"):
        async with make_object_store("blob-store"):  # type: ignore[arg-type]
            pass


def test_config_is_frozen_dataclass() -> None:
    config = S3CompatibleConfig(
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        bucket="b",
        access_key="ak",
        secret_key="sk",
    )
    # frozen=True → assignment raises FrozenInstanceError (a subclass of
    # AttributeError, which is what dataclasses raises).
    with pytest.raises(AttributeError):
        config.bucket = "other"  # type: ignore[misc]
