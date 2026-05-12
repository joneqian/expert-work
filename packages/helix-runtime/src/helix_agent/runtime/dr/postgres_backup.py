"""``PostgresFullBackup`` — pg_dump → ObjectStore → BackupRecordStore.

Design: subsystems/22-disaster-recovery § 4.1 / § 5.1 (M0 row).

M0 single-tenant dogfood path:
1. Allocate an ``asset_ref`` (timestamped key under ``bucket_prefix``).
2. Write a ``BackupRecord(status=RUNNING)`` so external observers can see
   the backup is in flight (and the RPO freshness gauge stays accurate).
3. Shell out to ``pg_dump -Fc`` writing the binary archive to a temp file.
4. Compute SHA-256 + size of the dump.
5. Upload the dump bytes to the ``ObjectStore``.
6. Upsert the ``BackupRecord`` to ``SUCCESS`` (or ``FAILED`` on any error)
   carrying ``finished_at`` / ``sha256`` / ``size_bytes`` / ``error``.

Loading the full dump into memory is acceptable at dogfood scale (a few
hundred MB at most). M1 swaps to multipart streaming via ``ObjectStore``
once that capability lands (ADR-0004 § 3 deferral).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from helix_agent.protocol import (
    BackupAssetType,
    BackupRecord,
    BackupStatus,
    BackupTier,
)

if TYPE_CHECKING:
    from helix_agent.persistence.dr import BackupRecordStore
    from helix_agent.runtime.storage import ObjectStore

logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    """Raised when a backup attempt fails — propagates to the caller after
    the FAILED ``BackupRecord`` has been persisted."""


@dataclass(frozen=True)
class PostgresBackupConfig:
    """Operator-visible knobs for one backup run.

    ``dsn`` must use the ``postgresql://`` (libpq) scheme — that's what
    ``pg_dump`` accepts. Note this is *not* the SQLAlchemy ``+asyncpg``
    DSN used elsewhere in the codebase; callers convert at the boundary.
    """

    dsn: str
    bucket_prefix: str = "backups/postgres"
    region: str = "local"
    pg_dump_cmd: str = "pg_dump"


DumpFn = Callable[[Path], Awaitable[None]]
"""Strategy for producing a ``pg_dump`` artifact at a given path.

Production wires this to a real ``pg_dump`` subprocess; tests inject a
stub that writes fixed bytes so the rest of the orchestration can run
without a live Postgres / system ``pg_dump`` binary.
"""


class PostgresFullBackup:
    """One Postgres full-backup pipeline.

    Stateless across runs — each ``run()`` allocates a fresh
    ``asset_ref`` and writes a single ``backup_record`` row.
    """

    asset_type = BackupAssetType.POSTGRES_FULL
    tier = BackupTier.TIER_0

    def __init__(
        self,
        config: PostgresBackupConfig,
        object_store: ObjectStore,
        record_store: BackupRecordStore,
        dump_fn: DumpFn | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._objects = object_store
        self._records = record_store
        self._dump_fn = dump_fn or self._default_dump_fn()
        self._clock = clock

    async def run(self) -> BackupRecord:
        """Execute one backup attempt and return the final ``BackupRecord``.

        :raises BackupError: on any subprocess / I/O failure. The FAILED
            record is persisted before the exception propagates so the
            freshness alert (``latest()`` + ``status=FAILED``) can react.
        """
        started_at = self._clock()
        asset_ref = self._allocate_asset_ref(started_at)

        await self._records.record(
            BackupRecord(
                asset_type=self.asset_type,
                asset_ref=asset_ref,
                started_at=started_at,
                status=BackupStatus.RUNNING,
                region=self._config.region,
                tier=self.tier,
            )
        )

        try:
            data, sha = await self._produce_dump_bytes()
            await self._objects.put(asset_ref, data, content_type="application/octet-stream")
        except Exception as exc:
            await self._records.record(
                BackupRecord(
                    asset_type=self.asset_type,
                    asset_ref=asset_ref,
                    started_at=started_at,
                    finished_at=self._clock(),
                    status=BackupStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    region=self._config.region,
                    tier=self.tier,
                )
            )
            logger.error(
                "dr.postgres_backup.failed asset_ref=%s reason=%s",
                asset_ref,
                exc,
            )
            raise BackupError(str(exc)) from exc

        success_record = await self._records.record(
            BackupRecord(
                asset_type=self.asset_type,
                asset_ref=asset_ref,
                started_at=started_at,
                finished_at=self._clock(),
                size_bytes=len(data),
                sha256=sha,
                status=BackupStatus.SUCCESS,
                region=self._config.region,
                tier=self.tier,
            )
        )
        logger.info(
            "dr.postgres_backup.success asset_ref=%s size_bytes=%d sha256=%s",
            asset_ref,
            len(data),
            sha,
        )
        return success_record

    def _allocate_asset_ref(self, started_at: datetime) -> str:
        # Sortable + collision-free at second granularity; M1 may switch
        # to a UUID suffix when we run multiple jobs per minute.
        ts = started_at.strftime("%Y%m%dT%H%M%SZ")
        return f"{self._config.bucket_prefix}/{ts}.dump"

    async def _produce_dump_bytes(self) -> tuple[bytes, str]:
        """Run the dump strategy to a temp file, then return ``(bytes, sha256_hex)``."""
        # ``tempfile.NamedTemporaryFile`` returns a sync file object; use the
        # path string and clean up explicitly so the cross-process pg_dump
        # subprocess can write to it without fighting the asyncio loop.
        fd, path_str = tempfile.mkstemp(suffix=".dump", prefix="helix-pgdump-")
        os.close(fd)
        path = Path(path_str)
        try:
            await self._dump_fn(path)
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            return data, sha
        finally:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def _default_dump_fn(self) -> DumpFn:
        """Real ``pg_dump`` invocation. Uses ``-Fc`` (custom format) which
        is the format ``pg_restore`` expects and the most space-efficient.
        """

        async def run(target: Path) -> None:
            with target.open("wb") as fh:
                proc = await asyncio.create_subprocess_exec(
                    self._config.pg_dump_cmd,
                    "-Fc",
                    "-d",
                    self._config.dsn,
                    stdout=fh,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
            if proc.returncode != 0:
                msg = (
                    f"pg_dump exited with code {proc.returncode}: "
                    f"{stderr.decode('utf-8', errors='replace').strip()}"
                )
                raise BackupError(msg)

        return run
