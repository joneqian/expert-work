"""Unit tests for the Keycloak admin-secret seed CLI — Stream R W4.

The DB wiring is exercised by the live runbook; here we cover the pure pieces:
value resolution precedence + that the seed writes to the vault under the
configured name.
"""

from __future__ import annotations

import pytest

from control_plane.seed_keycloak_secret import (
    SeedValueMissingError,
    resolve_secret_value,
    seed_keycloak_admin_secret,
)


def test_resolve_prefers_arg_over_env() -> None:
    got = resolve_secret_value("from-arg", env={"HELIX_AGENT_KEYCLOAK_ADMIN_CLIENT_SECRET": "env"})
    assert got == "from-arg"


def test_resolve_falls_back_to_env() -> None:
    got = resolve_secret_value(None, env={"HELIX_AGENT_KEYCLOAK_ADMIN_CLIENT_SECRET": "env-secret"})
    assert got == "env-secret"


def test_resolve_treats_empty_arg_as_absent() -> None:
    got = resolve_secret_value("", env={"HELIX_AGENT_KEYCLOAK_ADMIN_CLIENT_SECRET": "env-secret"})
    assert got == "env-secret"


def test_resolve_raises_when_nothing_supplied() -> None:
    with pytest.raises(SeedValueMissingError):
        resolve_secret_value(None, env={})


class _FakeStore:
    """Minimal SecretStore double recording the last ``put``."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, str]] = []

    async def put(self, name: str, value: str) -> None:
        self.puts.append((name, value))


@pytest.mark.asyncio
async def test_seed_writes_under_configured_name() -> None:
    store = _FakeStore()
    await seed_keycloak_admin_secret(
        store,  # type: ignore[arg-type]
        name="helix-agent/platform/keycloak/admin-client-secret",
        value="sekret",
    )
    assert store.puts == [("helix-agent/platform/keycloak/admin-client-secret", "sekret")]
