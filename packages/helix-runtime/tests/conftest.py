"""Shared fixtures for ``helix-runtime`` integration tests.

The docker-compose stack used by both ``test_minio_integration.py`` and
``test_minio_object_lock_integration.py`` is session-scoped so MinIO is
booted exactly once per pytest run. Previously each file owned its own
module-scoped fixture which meant the stack was torn down and re-spun
between files — slower and prone to a race where the second ``up``
collided with a not-yet-fully-stopped container.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.compose import DockerCompose

_INFRA_DIR = Path(__file__).resolve().parents[3] / "infra"


@pytest.fixture(scope="session")
def compose_stack() -> Iterator[DockerCompose]:
    """Boot the infra/docker-compose stack for the full pytest session."""
    stack = DockerCompose(
        context=str(_INFRA_DIR),
        compose_file_name="docker-compose.yml",
        pull=True,
        wait=True,
    )
    with stack:
        yield stack
