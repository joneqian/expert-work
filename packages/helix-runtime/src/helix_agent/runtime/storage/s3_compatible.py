"""``S3CompatibleObjectStore`` — aiobotocore-backed implementation.

Works against MinIO (local dev), Aliyun OSS (prod / staging), AWS S3,
Tencent COS, and any other S3-compatible endpoint. The choice of backend
is communicated entirely through ``endpoint_url`` + region + credentials
at construction time; no application code changes for migrations.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

from helix_agent.runtime.storage.base import ObjectNotFoundError, ObjectStoreError

logger = logging.getLogger(__name__)


class S3CompatibleObjectStore:
    """Operate on one S3-style bucket via an injected aiobotocore client.

    The client lifecycle is managed by the factory (``make_object_store``)
    so this class stays a pure operations surface — no connection state,
    no global session. ``client`` is typed ``Any`` because aiobotocore
    ships no py.typed marker; the runtime methods used are the standard
    S3 API and are stable across botocore releases.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "Body": data}
        if content_type is not None:
            kwargs["ContentType"] = content_type
        if metadata:
            # S3 metadata keys must be ASCII; let the SDK validate. Pass
            # through verbatim — callers carry the contract.
            kwargs["Metadata"] = dict(metadata)
        await self._client.put_object(**kwargs)

    async def get(self, key: str) -> bytes:
        try:
            response = await self._client.get_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.NoSuchKey as exc:
            msg = f"object not found: {key!r}"
            raise ObjectNotFoundError(msg) from exc

        async with response["Body"] as stream:
            data: bytes = await stream.read()
            return data

    async def delete(self, key: str) -> None:
        # S3 DeleteObject is idempotent — no error when key is absent —
        # matching the Protocol contract.
        await self._client.delete_object(Bucket=self._bucket, Key=key)

    async def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                k = obj.get("Key")
                if k is not None:
                    keys.append(k)
        keys.sort()
        return keys

    async def presigned_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        method: Literal["GET", "PUT"] = "GET",
    ) -> str:
        operation = "get_object" if method == "GET" else "put_object"
        try:
            url = await self._client.generate_presigned_url(
                ClientMethod=operation,
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            # ``generate_presigned_url`` shouldn't raise for valid inputs,
            # but credentials / endpoint misconfig surface here; wrap so
            # callers see one exception class.
            msg = f"failed to sign URL for key={key!r}: {exc}"
            raise ObjectStoreError(msg) from exc

        # botocore returns ``str``; assert for the type checker since the
        # stub types are slightly loose.
        if not isinstance(url, str):  # pragma: no cover — defensive
            msg = f"expected str URL, got {type(url).__name__}"
            raise ObjectStoreError(msg)
        return url
