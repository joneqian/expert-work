"""Skill supporting-file asset store (skill-asset-store).

Large skills are asset libraries — anthropics-style reference skills bundle
fonts, icon sets and Office templates far beyond what the
``skill_version.supporting_files`` JSONB row can (or should) hold inline
(live driver: hugohe3/ppt-master — 12k files / ~61 MB). This module moves
supporting-file BYTES into the platform object store and leaves only a
manifest entry (``storage_key`` + ``sha256`` + size/mime) in the row.

Design points:

* **Content-addressed keys** — ``skill-assets/sha256/<hex>``. Identical
  bytes dedupe across skills / versions / re-imports for free; objects are
  immutable so there is nothing to invalidate. No GC in scope: orphans are
  harmless and shared objects must never be deleted per-version anyway.
* **Dual-read** — inline (legacy) entries keep working untouched;
  :func:`fetch_supporting_file` is the single reader both shapes go
  through. Rows never need a schema migration.
* **Integrity** — the version's ``content_hash`` covers the manifest
  (``storage_key``/``sha256`` participate — see
  ``supporting_files_to_jsonable``); every fetch re-verifies the object
  against the entry's ``sha256``.
* **Fallback** — with no object store configured everything stays inline,
  bounded by the tighter inline caps (see ``_skill_zip``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections.abc import Mapping
from typing import Protocol

from expert_work.protocol.skill import SkillSupportingFile

logger = logging.getLogger("expert_work.runtime.skill_assets")

#: Content-addressed key prefix — see module docstring.
ASSET_KEY_PREFIX = "skill-assets/sha256/"

#: Concurrent object-store puts/gets per operation. MinIO/S3 handle far
#: more, but each in-flight put pins the file's bytes in memory.
_IO_CONCURRENCY = 32


class ObjectStore(Protocol):
    """Structural subset of the runtime object store this module needs."""

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None: ...

    async def get(self, key: str) -> bytes: ...


class SkillAssetError(RuntimeError):
    """Base for external skill-asset read failures."""


class SkillAssetIntegrityError(SkillAssetError):
    """Fetched object bytes do not match the manifest ``sha256``."""


class SkillAssetUnavailableError(SkillAssetError):
    """No object store configured, or the store/object is unreachable."""


def asset_key(digest_hex: str) -> str:
    return f"{ASSET_KEY_PREFIX}{digest_hex}"


async def externalize_supporting_files(
    files: Mapping[str, SkillSupportingFile],
    *,
    object_store: ObjectStore | None,
) -> dict[str, SkillSupportingFile]:
    """Upload every inline entry's bytes; return external-shape entries.

    ``object_store is None`` → returned unchanged (inline fallback).
    Already-external entries pass through. Idempotent: content-addressed
    keys make a re-put of identical bytes a no-op overwrite, so a retry
    after a partial failure never corrupts anything.

    Called BEFORE the content hash is computed — the hash must cover the
    exact shape that gets persisted.
    """
    if object_store is None or not files:
        return dict(files)

    sem = asyncio.Semaphore(_IO_CONCURRENCY)

    async def _one(path: str, sf: SkillSupportingFile) -> tuple[str, SkillSupportingFile]:
        if sf.is_external:
            return path, sf
        raw = base64.b64decode(sf.content)
        digest = hashlib.sha256(raw).hexdigest()
        key = asset_key(digest)
        async with sem:
            await object_store.put(key, raw, content_type=sf.mime or None)
        return path, SkillSupportingFile(
            content="",
            size=sf.size,
            mime=sf.mime,
            storage_key=key,
            sha256=digest,
        )

    pairs = await asyncio.gather(*(_one(p, sf) for p, sf in files.items()))
    return dict(pairs)


async def fetch_supporting_file(
    sf: SkillSupportingFile,
    *,
    object_store: ObjectStore | None,
) -> bytes:
    """Raw bytes of one entry — the single dual-read path.

    Inline → base64-decode. External → object-store get + sha256 verify.
    """
    if not sf.is_external:
        # validate=True so corrupted rows raise (binascii.Error) instead of
        # silently decoding garbage — callers map that to a "bad_base64" drop.
        return base64.b64decode(sf.content, validate=True)
    if object_store is None:
        msg = (
            f"supporting file is externalized ({sf.storage_key}) but no object store is configured"
        )
        raise SkillAssetUnavailableError(msg)
    storage_key = sf.storage_key
    if storage_key is None:  # pragma: no cover — narrowed by is_external
        msg = "external entry without a storage_key"
        raise SkillAssetUnavailableError(msg)
    try:
        raw = await object_store.get(storage_key)
    except Exception as exc:
        msg = f"object store get failed for {storage_key!r}: {exc}"
        raise SkillAssetUnavailableError(msg) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sf.sha256:
        msg = f"object {sf.storage_key!r} digest mismatch: got {digest}, manifest says {sf.sha256}"
        raise SkillAssetIntegrityError(msg)
    return raw


async def fetch_supporting_files(
    files: Mapping[str, SkillSupportingFile],
    *,
    object_store: ObjectStore | None,
) -> dict[str, bytes]:
    """Batch dual-read with bounded concurrency (sandbox seeding, export)."""
    sem = asyncio.Semaphore(_IO_CONCURRENCY)

    async def _one(path: str, sf: SkillSupportingFile) -> tuple[str, bytes]:
        async with sem:
            return path, await fetch_supporting_file(sf, object_store=object_store)

    pairs = await asyncio.gather(*(_one(p, sf) for p, sf in files.items()))
    return dict(pairs)
