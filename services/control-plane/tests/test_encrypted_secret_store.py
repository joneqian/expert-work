"""Tests for the encrypted-secret vault — Stream Q (PR B).

Cipher tests are pure (no DB). The store round-trip tests are ``integration``
(real Postgres via testcontainers) because the store encrypts into the
``encrypted_secret`` table created by migration 0050.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from testcontainers.postgres import PostgresContainer

from control_plane.encrypted_secret_store import (
    EnvelopeCipher,
    SqlEncryptedSecretStore,
    build_kek_from_b64,
)
from expert_work.persistence import (
    DatabaseConfig,
    build_rls_sessionmaker,
    create_async_engine_from_config,
    create_async_session_factory,
)
from expert_work.runtime.secret_store.base import SecretNotFoundError, SecretStoreError

_NAME = "expert-work/platform/llm/anthropic"


# --------------------------------------------------------------------- cipher


def test_cipher_round_trip() -> None:
    cipher = EnvelopeCipher(os.urandom(32))
    nonce, ciphertext = cipher.encrypt(name=_NAME, plaintext="sk-ant-secret")
    assert b"sk-ant-secret" not in ciphertext  # encrypted, not stored raw
    assert cipher.decrypt(name=_NAME, nonce=nonce, ciphertext=ciphertext) == "sk-ant-secret"


def test_cipher_fresh_nonce_per_encrypt() -> None:
    cipher = EnvelopeCipher(os.urandom(32))
    n1, _ = cipher.encrypt(name=_NAME, plaintext="x")
    n2, _ = cipher.encrypt(name=_NAME, plaintext="x")
    assert n1 != n2


def test_cipher_wrong_kek_fails() -> None:
    nonce, ciphertext = EnvelopeCipher(os.urandom(32)).encrypt(name=_NAME, plaintext="x")
    with pytest.raises(SecretStoreError):
        EnvelopeCipher(os.urandom(32)).decrypt(name=_NAME, nonce=nonce, ciphertext=ciphertext)


def test_cipher_aad_binds_name() -> None:
    cipher = EnvelopeCipher(os.urandom(32))
    nonce, ciphertext = cipher.encrypt(name=_NAME, plaintext="x")
    with pytest.raises(SecretStoreError):
        cipher.decrypt(name="other-name", nonce=nonce, ciphertext=ciphertext)


def test_build_kek_validation() -> None:
    assert len(build_kek_from_b64(base64.b64encode(os.urandom(32)).decode())) == 32
    with pytest.raises(ValueError, match="32 bytes"):
        build_kek_from_b64(base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(ValueError, match="base64"):
        build_kek_from_b64("not!valid!base64!")


# ---------------------------------------------------------- backend selection


def test_build_secret_store_local_dev_default() -> None:
    from control_plane.app import _build_secret_store
    from control_plane.settings import Settings
    from expert_work.runtime.secret_store.local_dev import LocalDevSecretStore

    store = _build_secret_store(Settings(secret_store_backend="local_dev"), None)
    assert isinstance(store, LocalDevSecretStore)


def test_build_secret_store_sql_encrypted_requires_sql_backend() -> None:
    from control_plane.app import _build_secret_store
    from control_plane.settings import Settings

    with pytest.raises(RuntimeError, match="store_backend='sql'"):
        _build_secret_store(Settings(secret_store_backend="sql_encrypted"), None)


def test_build_secret_store_sql_encrypted_requires_kek() -> None:
    from control_plane.app import _build_secret_store
    from control_plane.settings import Settings

    # A truthy stand-in for sql_stores so the missing-KEK branch is reached.
    fake_sql_stores = object()
    with pytest.raises(RuntimeError, match="SECRET_ENCRYPTION_KEY"):
        _build_secret_store(
            Settings(secret_store_backend="sql_encrypted"),
            fake_sql_stores,  # type: ignore[arg-type]
        )


# ----------------------------------------------------------------- store (PG)

ALEMBIC_INI = (
    Path(__file__).resolve().parents[3] / "packages" / "expert-work-persistence" / "alembic.ini"
)


def _sync_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+psycopg").replace("postgresql://", "postgresql+psycopg://", 1)


def _async_dsn(c: PostgresContainer) -> str:
    url = str(c.get_connection_url())
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture
def vault(
    postgres_container: PostgresContainer,
) -> Iterator[tuple[SqlEncryptedSecretStore, AsyncEngine]]:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", _sync_dsn(postgres_container))
    command.upgrade(cfg, "head")
    engine = create_async_engine_from_config(DatabaseConfig(dsn=_async_dsn(postgres_container)))
    sf = build_rls_sessionmaker(create_async_session_factory(engine))
    yield SqlEncryptedSecretStore(sf, kek=os.urandom(32)), engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_get_round_trip_and_ciphertext_at_rest(
    vault: tuple[SqlEncryptedSecretStore, AsyncEngine],
) -> None:
    store, engine = vault
    # Distinct name per integration test — the postgres_container fixture is
    # shared, so rows persist across tests in the suite.
    name = "expert-work/platform/llm/roundtrip"
    try:
        await store.put(name, "sk-ant-REAL")
        assert await store.get(name) == "sk-ant-REAL"

        # The value at rest is ciphertext — the plaintext must not appear in any
        # column of the row.
        async with engine.connect() as conn:
            stmt = text("SELECT ciphertext FROM encrypted_secret WHERE name=:n")
            row = (await conn.execute(stmt, {"n": name})).one()
        assert b"sk-ant-REAL" not in bytes(row[0])
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repaste_versions_and_current(
    vault: tuple[SqlEncryptedSecretStore, AsyncEngine],
) -> None:
    store, engine = vault
    name = "expert-work/platform/llm/repaste"  # distinct — shared container
    try:
        await store.put(name, "key-v1")
        await store.put(name, "key-v2")
        # get() returns the latest (current) version.
        assert await store.get(name) == "key-v2"
        versions = await store.list_versions(name)
        assert len(versions) == 2
        # Both versions are independently fetchable and decrypt to the two values
        # (order-independent so the test doesn't flake on equal created_at).
        assert {await store.get(name, version=v) for v in versions} == {"key-v1", "key-v2"}
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_secret_raises(
    vault: tuple[SqlEncryptedSecretStore, AsyncEngine],
) -> None:
    store, engine = vault
    try:
        with pytest.raises(SecretNotFoundError):
            await store.get("expert-work/platform/llm/nope")
        with pytest.raises(SecretNotFoundError):
            await store.list_versions("expert-work/platform/llm/nope")
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_removes_all_versions(
    vault: tuple[SqlEncryptedSecretStore, AsyncEngine],
) -> None:
    store, engine = vault
    name = "expert-work/platform/llm/delete-me"  # distinct — shared container
    try:
        await store.put(name, "key-v1")
        await store.put(name, "key-v2")

        await store.delete(name)

        with pytest.raises(SecretNotFoundError):
            await store.get(name)
        with pytest.raises(SecretNotFoundError):
            await store.list_versions(name)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_missing_secret_is_idempotent(
    vault: tuple[SqlEncryptedSecretStore, AsyncEngine],
) -> None:
    store, engine = vault
    try:
        await store.delete("expert-work/platform/llm/never-existed")  # does NOT raise
    finally:
        await engine.dispose()
