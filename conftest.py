"""Root pytest configuration — fixtures usable by every test in the repo.

Helper classes live in :mod:`helix_agent.testing` for direct importability;
this module only registers the pytest fixtures.

Per-Stream additions:
- Stream A.1 introduced the ``postgres_container`` fixture consumer
  (``packages/helix-persistence/tests/test_initial_schema.py``)
- Stream E.1 will introduce VCR-recorded Anthropic cassettes
- ADR-0007 SecretStore Protocol will be added in Stream A.x;
  ``mock_secret_store`` will then implement it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from helix_agent.testing import InMemorySecretStore, MockLLM

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


@pytest.fixture
def mock_llm() -> MockLLM:
    """Fresh MockLLM per test."""
    return MockLLM()


@pytest.fixture
def mock_secret_store() -> InMemorySecretStore:
    """Fresh InMemorySecretStore per test."""
    return InMemorySecretStore()


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Session-scoped Postgres 16 container via testcontainers.

    Heavy — adds ~10s session startup. First consumer is Stream A.1
    (``packages/helix-persistence/tests/test_initial_schema.py``).

    Uses the ``pgvector/pgvector`` image (Postgres 16 + the ``vector``
    extension, Stream J.3) — a superset of stock Postgres, so every
    pre-J.3 migration / test is unaffected.

    Requires Docker daemon available; tests using this fixture should
    be marked ``@pytest.mark.integration``.
    """
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("pgvector/pgvector:pg16")
    with container:
        yield container


@pytest.fixture
def tmp_postgres_dsn(postgres_container: PostgresContainer) -> str:
    """SQLAlchemy-compatible DSN for the session Postgres container."""
    return str(postgres_container.get_connection_url())
