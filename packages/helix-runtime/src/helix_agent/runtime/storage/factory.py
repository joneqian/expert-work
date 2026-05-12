"""Factory + async context manager for ``ObjectStore`` instances.

Mirrors the checkpointer / store / stream_bridge factory pattern: an
``asynccontextmanager`` that wires up the client(s) and tears down on
exit. Use in FastAPI lifespan or per-test fixture::

    async with make_object_store("s3-compatible", config) as store:
        await store.put("...", b"...")
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from helix_agent.runtime.storage.base import ObjectStore
from helix_agent.runtime.storage.memory import InMemoryObjectStore
from helix_agent.runtime.storage.s3_compatible import S3CompatibleObjectStore

ObjectStoreBackend = Literal["memory", "s3-compatible"]


@dataclass(frozen=True)
class S3CompatibleConfig:
    """All knobs needed to point aiobotocore at an S3-compatible endpoint.

    For MinIO local dev: ``endpoint_url="http://localhost:9000"``,
    ``use_path_style=True``. For Aliyun OSS prod: HTTPS endpoint,
    ``use_path_style=False`` (OSS supports virtual-host style by default).
    """

    endpoint_url: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    use_path_style: bool = True


@contextlib.asynccontextmanager
async def make_object_store(
    backend: ObjectStoreBackend,
    config: S3CompatibleConfig | None = None,
) -> AsyncIterator[ObjectStore]:
    """Yield a configured ``ObjectStore``; tear it down on exit.

    :param backend: ``"memory"`` (tests / dev fakes) or ``"s3-compatible"``
        (MinIO / OSS / S3).
    :param config: required when ``backend == "s3-compatible"``.

    :raises ValueError: backend unknown / config missing.
    """
    # Widen to ``str`` so the trailing "unknown backend" path is reachable
    # to both mypy and runtime (config-string callers are the typical
    # source of bad values).
    bk: str = backend
    if bk == "memory":
        yield InMemoryObjectStore()
        return

    if bk == "s3-compatible":
        if config is None:
            msg = "backend 's3-compatible' requires an S3CompatibleConfig"
            raise ValueError(msg)
        async with _build_s3_store(config) as store:
            yield store
        return

    msg = f"unknown object_store backend: {bk!r}"
    raise ValueError(msg)


@contextlib.asynccontextmanager
async def _build_s3_store(
    config: S3CompatibleConfig,
) -> AsyncIterator[S3CompatibleObjectStore]:
    # aiobotocore is a heavy import (~150ms warm); defer until needed so
    # callers using only the in-memory backend pay no startup cost.
    from aiobotocore.session import get_session
    from botocore.config import Config as BotoConfig

    boto_config = BotoConfig(
        s3={"addressing_style": "path" if config.use_path_style else "auto"},
        signature_version="s3v4",
    )

    session = get_session()
    async with session.create_client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        config=boto_config,
    ) as client:
        yield S3CompatibleObjectStore(client=client, bucket=config.bucket)
