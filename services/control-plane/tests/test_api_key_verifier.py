"""Unit tests for :class:`control_plane.auth.ApiKeyVerifier`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from argon2 import PasswordHasher

from control_plane.auth import (
    API_KEY_PREFIX_LEN,
    API_KEY_SENTINEL,
    ApiKeyVerifier,
    InvalidTokenError,
    TokenExpiredError,
    is_api_key_bearer,
    mint_api_key,
)
from expert_work.persistence.auth import InMemoryApiKeyStore
from expert_work.protocol import ApiKeyScope

_TENANT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_SA_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ---------------------------------------------------------------------------
# mint + sentinel
# ---------------------------------------------------------------------------


def test_mint_produces_well_formed_bearer() -> None:
    gen = mint_api_key(tenant_id=_TENANT)
    assert gen.plaintext.startswith(API_KEY_SENTINEL)
    assert len(gen.prefix) == API_KEY_PREFIX_LEN
    # Tenant short visible in the prefix.
    assert _TENANT.hex[:5] in gen.prefix
    # Hash starts with the argon2id header.
    assert gen.secret_hash.startswith("$argon2id$")


def test_is_api_key_bearer_recognises_sentinel() -> None:
    assert is_api_key_bearer("aforge_pat_abc")
    assert not is_api_key_bearer("eyJ.jwt.bits")
    assert not is_api_key_bearer("")


# ---------------------------------------------------------------------------
# verifier happy path
# ---------------------------------------------------------------------------


async def _build_store_with_key(
    *,
    expires_at: datetime | None = None,
    revoked: bool = False,
    scopes: tuple[ApiKeyScope, ...] = (ApiKeyScope.READ,),
) -> tuple[InMemoryApiKeyStore, str]:
    store = InMemoryApiKeyStore()
    gen = mint_api_key(tenant_id=_TENANT)
    key = await store.create(
        tenant_id=_TENANT,
        service_account_id=_SA_ID,
        prefix=gen.prefix,
        secret_hash=gen.secret_hash,
        scopes=scopes,
        expires_at=expires_at,
        created_by="admin",
    )
    if revoked:
        await store.revoke(tenant_id=_TENANT, api_key_id=key.id)
    return store, gen.plaintext


@pytest.mark.asyncio
async def test_valid_bearer_yields_service_account_principal() -> None:
    store, plaintext = await _build_store_with_key()
    verifier = ApiKeyVerifier.from_store(store)
    principal = await verifier.verify(plaintext)
    assert principal.subject_id == str(_SA_ID)
    assert principal.subject_type == "service_account"
    assert principal.tenant_id == _TENANT
    assert principal.auth_method == "api_key"
    assert principal.scopes == (ApiKeyScope.READ.value,)


@pytest.mark.asyncio
async def test_unknown_prefix_returns_invalid_token() -> None:
    store = InMemoryApiKeyStore()
    verifier = ApiKeyVerifier.from_store(store)
    with pytest.raises(InvalidTokenError):
        await verifier.verify("aforge_pat_zzzzz_doesnotexistdoesnotexist")


@pytest.mark.asyncio
async def test_wrong_secret_returns_invalid_token() -> None:
    store, plaintext = await _build_store_with_key()
    verifier = ApiKeyVerifier.from_store(store)
    # Tamper the tail bytes.
    bad = plaintext[:API_KEY_PREFIX_LEN] + "x" * 32
    with pytest.raises(InvalidTokenError):
        await verifier.verify(bad)


@pytest.mark.asyncio
async def test_revoked_key_returns_invalid_token() -> None:
    store, plaintext = await _build_store_with_key(revoked=True)
    verifier = ApiKeyVerifier.from_store(store)
    with pytest.raises(InvalidTokenError):
        await verifier.verify(plaintext)


@pytest.mark.asyncio
async def test_expired_key_returns_token_expired() -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    store, plaintext = await _build_store_with_key(expires_at=past)
    verifier = ApiKeyVerifier.from_store(store)
    with pytest.raises(TokenExpiredError):
        await verifier.verify(plaintext)


@pytest.mark.asyncio
async def test_non_api_key_bearer_raises_invalid_token() -> None:
    store = InMemoryApiKeyStore()
    verifier = ApiKeyVerifier.from_store(store)
    with pytest.raises(InvalidTokenError):
        await verifier.verify("eyJ.notarealjwt")


@pytest.mark.asyncio
async def test_short_bearer_raises_invalid_token() -> None:
    store = InMemoryApiKeyStore()
    verifier = ApiKeyVerifier.from_store(store)
    with pytest.raises(InvalidTokenError):
        await verifier.verify("aforge_pat_")


@pytest.mark.asyncio
async def test_last_used_timestamp_is_updated_on_success() -> None:
    store, plaintext = await _build_store_with_key()
    verifier = ApiKeyVerifier.from_store(store)
    await verifier.verify(plaintext)
    # Fetch the row via prefix; ``last_used_at`` must now be set.
    prefix = plaintext[:API_KEY_PREFIX_LEN]
    record = await store.get_by_prefix(prefix=prefix)
    assert record is not None
    assert record.last_used_at is not None


@pytest.mark.asyncio
async def test_explicit_hasher_is_reused() -> None:
    """The same PasswordHasher instance should be reusable across verifies."""
    hasher = PasswordHasher()
    store = InMemoryApiKeyStore()
    gen = mint_api_key(tenant_id=_TENANT, hasher=hasher)
    await store.create(
        tenant_id=_TENANT,
        service_account_id=_SA_ID,
        prefix=gen.prefix,
        secret_hash=gen.secret_hash,
        scopes=(),
        expires_at=None,
        created_by="admin",
    )
    verifier = ApiKeyVerifier(store=store, hasher=hasher)
    principal = await verifier.verify(gen.plaintext)
    assert principal.subject_type == "service_account"
