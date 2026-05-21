"""Docker engine access — the supervisor's only OS-process surface.

A :class:`DockerClient` Protocol so the :class:`SandboxSupervisor` logic
is unit-testable with a recording fake (test matrix #40); the real
:class:`CliDockerClient` shells out to the ``docker`` CLI.

The C-model (STREAM-F-DESIGN): ``launch`` starts ``docker run -i`` and
*keeps the subprocess alive*, handing back a :class:`RunnerLink` over its
stdio — the supervisor talks to the in-sandbox runner through that pipe.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from sandbox_supervisor.runner_link import PipeRunnerLink, RunnerLink

logger = logging.getLogger(__name__)

#: ``asyncio`` StreamReader buffer for a runner's stdout. Generous so a
#: large (runner-capped, ~1 MiB) response line never overflows the
#: default 64 KiB limit.
_READ_LIMIT = 4 * 1024 * 1024

#: ``docker run`` argv prefix used to find leftover sandboxes on boot.
_ORPHAN_NAME_PREFIX = "helix-sb-"


class DockerError(RuntimeError):
    """A ``docker`` CLI invocation failed."""


class DockerClient(Protocol):
    """The Docker operations the supervisor needs — nothing more."""

    async def launch(self, argv: list[str]) -> RunnerLink:
        """Start a ``docker run -i`` container; return a live link to it."""

    async def remove(self, container_name: str) -> None:
        """Force-remove a container (``docker rm --force``)."""

    async def ping(self) -> bool:
        """Return whether the Docker daemon is reachable."""

    async def sweep_orphans(self) -> int:
        """Remove leftover ``helix-sb-*`` containers; return the count."""

    async def read_volume_file(
        self, *, volume: str, path: str, image: str, max_bytes: int
    ) -> bytes:
        """Read a file from a named volume; return up to ``max_bytes + 1`` bytes."""

    async def measure_volume_size(self, *, volume: str, image: str) -> int:
        """Return the total ``/workspace`` size in bytes for a named volume.

        Stream J.15-补强-1 — backs :class:`QuotaEnforcer.refresh_size`.
        Runs a throwaway ``--rm`` container that mounts ``volume`` read-
        only and runs ``du -sb``. Returns the apparent total bytes. Raises
        :class:`DockerError` when the measure can't run.
        """

    async def archive_volume(self, *, volume: str, image: str, max_bytes: int) -> bytes:
        """Stream a tar.gz snapshot of a named volume into memory.

        Stream J.15-补强-2 — backs the J.15 lifecycle archive + daily
        backup pipelines. Spawns a throwaway ``--rm`` container that
        mounts ``volume`` read-only and runs ``tar -czf - .`` from
        ``/ws``. ``max_bytes`` caps the buffer so a runaway / malicious
        volume can't OOM the supervisor; over-cap archives raise
        :class:`DockerError`. Raises on any non-zero exit.
        """

    async def remove_volume(self, *, volume: str) -> None:
        """Force-remove a named volume (``docker volume rm --force``).

        Stream J.15-补强-2 — called after a successful archive to free
        the disk. Idempotent — removing a missing volume is logged but
        does not raise.
        """


class CliDockerClient:
    """:class:`DockerClient` backed by the ``docker`` CLI via asyncio subprocess."""

    async def launch(self, argv: list[str]) -> RunnerLink:
        # `docker run -i` (no -d): the subprocess stays alive for the
        # container's lifetime and its stdio *is* the container's stdio.
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_READ_LIMIT,
        )
        return PipeRunnerLink(process)

    async def remove(self, container_name: str) -> None:
        _, stderr, code = await self._exec(["docker", "rm", "--force", container_name])
        if code != 0:
            # A missing container is fine — destroy is idempotent.
            logger.warning(
                "docker_client.remove_failed container=%s reason=%s",
                container_name,
                stderr.strip(),
            )

    async def ping(self) -> bool:
        try:
            _, _, code = await self._exec(["docker", "version", "--format", "{{.Server.Version}}"])
        except OSError:
            return False
        return code == 0

    async def sweep_orphans(self) -> int:
        stdout, _, code = await self._exec(
            ["docker", "ps", "--all", "--quiet", "--filter", f"name={_ORPHAN_NAME_PREFIX}"]
        )
        if code != 0:
            return 0
        ids = [line for line in stdout.split() if line]
        for container_id in ids:
            await self.remove(container_id)
        if ids:
            logger.info("docker_client.swept_orphans count=%d", len(ids))
        return len(ids)

    async def read_volume_file(
        self, *, volume: str, path: str, image: str, max_bytes: int
    ) -> bytes:
        """Read ``path`` from a docker named volume (Stream J.9).

        Runs a throwaway ``--rm`` container — read-only rootfs, no
        network, all capabilities dropped — that mounts ``volume`` at
        ``/ws`` read-only and ``head``s the file. ``head -c`` bounds the
        output (and the supervisor's buffer) at ``max_bytes + 1`` bytes,
        so the caller can detect an over-cap file. Raises
        :class:`DockerError` when the file cannot be read.

        ``--entrypoint head`` overrides the sandbox image's runner
        entrypoint — without it the runner would treat ``head ...`` as
        its own args and just print its readiness line.
        """
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--volume",
            f"{volume}:/ws:ro",
            "--entrypoint",
            "head",
            image,
            "-c",
            str(max_bytes + 1),
            f"/ws/{path}",
        ]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=max_bytes + 1024,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            msg = f"workspace file read failed for {path!r}: {detail}"
            raise DockerError(msg)
        return stdout

    async def measure_volume_size(self, *, volume: str, image: str) -> int:
        """Measure a named volume's total size in bytes (Stream J.15-补强-1).

        Spawns a throwaway ``--rm`` container — read-only rootfs, no
        network, all capabilities dropped — that mounts ``volume`` at
        ``/ws`` read-only and runs ``du -sb /ws`` (apparent bytes).
        Returns the parsed integer. Raises :class:`DockerError` when
        the measure fails (volume missing, du parse error, etc.).

        ``--entrypoint sh`` overrides the sandbox image's runner so the
        ``du -sb`` argv reaches busybox / coreutils.
        """
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--volume",
            f"{volume}:/ws:ro",
            "--entrypoint",
            "sh",
            image,
            "-c",
            "du -sb /ws | cut -f1",
        ]
        stdout, stderr, code = await self._exec(argv)
        if code != 0:
            detail = stderr.strip() or "non-zero exit"
            msg = f"volume size measure failed for {volume!r}: {detail}"
            raise DockerError(msg)
        try:
            return int(stdout.strip())
        except ValueError as exc:
            msg = f"could not parse du output for {volume!r}: {stdout!r}"
            raise DockerError(msg) from exc

    async def archive_volume(self, *, volume: str, image: str, max_bytes: int) -> bytes:
        """Stream a tar.gz of ``volume`` to stdout via a throwaway container.

        Stream J.15-补强-2. The container mounts ``volume`` read-only at
        ``/ws`` and emits ``tar -czf - .`` on stdout. We capture up to
        ``max_bytes + 1`` bytes — one over the cap so callers can detect
        an over-cap volume and raise. Hardening mirrors
        :meth:`read_volume_file`: no network, read-only rootfs, all caps
        dropped.
        """
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--volume",
            f"{volume}:/ws:ro",
            "--entrypoint",
            "sh",
            image,
            "-c",
            "cd /ws && tar -czf - .",
        ]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=max_bytes + 1024,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            msg = f"volume archive failed for {volume!r}: {detail}"
            raise DockerError(msg)
        if len(stdout) > max_bytes:
            msg = (
                f"volume archive for {volume!r} exceeds the in-memory cap "
                f"({len(stdout)} > {max_bytes} bytes); upgrade to multipart "
                "ObjectStore.put (推 M1) before enabling larger workspaces"
            )
            raise DockerError(msg)
        return stdout

    async def remove_volume(self, *, volume: str) -> None:
        """Force-remove a named volume; idempotent.

        Stream J.15-补强-2 — called after successful archive to free disk.
        """
        _, stderr, code = await self._exec(["docker", "volume", "rm", "--force", volume])
        if code != 0:
            # A missing volume is fine — archive may have raced with a
            # manual cleanup; the row's archived_object_key already
            # records the only durable copy.
            logger.warning(
                "docker_client.volume_remove_failed volume=%s reason=%s",
                volume,
                stderr.strip(),
            )

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
