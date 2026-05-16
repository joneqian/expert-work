"""Docker engine access — the supervisor's only OS-process surface.

A :class:`DockerClient` Protocol so the :class:`SandboxSupervisor` logic
is unit-testable with a recording fake (test matrix #40); the real
:class:`CliDockerClient` shells out to the ``docker`` CLI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class DockerError(RuntimeError):
    """A ``docker`` CLI invocation failed."""


class DockerClient(Protocol):
    """The Docker operations the supervisor needs — nothing more."""

    async def run(self, argv: list[str]) -> str:
        """Run a fully-formed ``docker run`` argv; return the container id."""

    async def remove(self, container_id: str) -> None:
        """Force-remove a container (``docker rm --force``) — kills if running."""

    async def ping(self) -> bool:
        """Return whether the Docker daemon is reachable."""


class CliDockerClient:
    """:class:`DockerClient` backed by the ``docker`` CLI via asyncio subprocess."""

    async def run(self, argv: list[str]) -> str:
        stdout, stderr, code = await self._exec(argv)
        if code != 0:
            msg = f"docker run failed (exit {code}): {stderr.strip()}"
            raise DockerError(msg)
        container_id = stdout.strip()
        if not container_id:
            msg = "docker run returned an empty container id"
            raise DockerError(msg)
        return container_id

    async def remove(self, container_id: str) -> None:
        _, stderr, code = await self._exec(["docker", "rm", "--force", container_id])
        if code != 0:
            # A missing container is fine — destroy is idempotent.
            logger.warning(
                "docker_client.remove_failed container=%s reason=%s",
                container_id,
                stderr.strip(),
            )

    async def ping(self) -> bool:
        try:
            _, _, code = await self._exec(["docker", "version", "--format", "{{.Server.Version}}"])
        except OSError:
            return False
        return code == 0

    @staticmethod
    async def _exec(argv: list[str]) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )
