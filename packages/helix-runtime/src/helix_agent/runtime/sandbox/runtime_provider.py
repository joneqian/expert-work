"""Hardened ``docker run`` argv construction for sandbox containers.

Stream F.3 — STREAM-F-DESIGN § 2.3 / Mini-ADR F-3 / F-5.

The Sandbox Supervisor (F.1) launches one container per ``exec_python``
call. *How* it is launched — the OCI runtime and the hardening flags —
is owned here, so a single place enforces the Mini-ADR F-5 checklist and
the dev (``runc``) vs prod (``runsc`` / gVisor) split is one config knob
rather than branching scattered across the supervisor.

subsystem 14 § 5.5: gVisor is Linux-only, so dev (incl. macOS) runs
``runc`` — it verifies sandbox *behaviour*, not isolation *strength*;
the gVisor isolation gates run on a Linux CI runner under ``runsc``.

The provider only *builds* the argv — it never calls Docker. That keeps
it pure and unit-testable (test matrix #43) and leaves process execution
to the supervisor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

#: OCI runtimes the sandbox supports. ``runc`` is Docker's default
#: (dev / macOS); ``runsc`` is gVisor (Linux prod).
SandboxOciRuntime = Literal["runc", "runsc"]

#: Docker network the sandbox attaches to. Egress from it is restricted
#: to the credential-proxy by an iptables allowlist (Mini-ADR F-2); the
#: network itself is created by Stream F.5.
DEFAULT_EGRESS_NETWORK = "helix-sandbox-egress"


@dataclass(frozen=True)
class SandboxResourceLimits:
    """Per-container resource caps. Defaults match STREAM-F-DESIGN § 2.3."""

    cpus: float = 1.0
    memory_mb: int = 512
    pids_limit: int = 128
    workspace_size_mb: int = 64


#: Default caps — a module-level singleton so it can be an argument
#: default without tripping flake8-bugbear B008 (it is frozen / immutable).
DEFAULT_RESOURCE_LIMITS = SandboxResourceLimits()


@dataclass(frozen=True)
class SandboxRuntimeProvider:
    """Builds the hardened ``docker run`` argv for one sandbox container.

    ``oci_runtime`` selects the runtime: ``runsc`` appends
    ``--runtime runsc``; ``runc`` is Docker's default and adds no flag.
    """

    oci_runtime: SandboxOciRuntime
    egress_network: str = DEFAULT_EGRESS_NETWORK

    def docker_run_argv(
        self,
        *,
        image: str,
        container_name: str,
        limits: SandboxResourceLimits = DEFAULT_RESOURCE_LIMITS,
    ) -> list[str]:
        """Return the full ``docker run`` argv for the sandbox.

        The argv carries the Mini-ADR F-5 runtime hardening: read-only
        rootfs, a single writable ``/workspace`` tmpfs, all capabilities
        dropped, ``no-new-privileges``, and PID / memory / CPU caps.
        ``--interactive`` keeps stdin open for the runner's line-JSON
        protocol; the image is the final argument.
        """
        argv = [
            "docker",
            "run",
            "--name",
            container_name,
            "--interactive",
            "--read-only",
            "--tmpfs",
            # mode=1777: the tmpfs root must be writable by the image's
            # non-root ``agent`` user — without it /workspace is root-owned
            # and the sandbox cannot create files (F.8 gate #1).
            f"/workspace:rw,size={limits.workspace_size_mb}m,mode=1777",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpus),
            "--network",
            self.egress_network,
        ]
        if self.oci_runtime == "runsc":
            argv += ["--runtime", "runsc"]
        argv.append(image)
        return argv


def make_sandbox_runtime_provider(
    oci_runtime: str,
    *,
    egress_network: str = DEFAULT_EGRESS_NETWORK,
) -> SandboxRuntimeProvider:
    """Build a :class:`SandboxRuntimeProvider`, validating ``oci_runtime``.

    ``oci_runtime`` is typed ``str`` (not :data:`SandboxOciRuntime`)
    because it arrives from ``environments/{env}.yaml`` — an arbitrary
    runtime string. An unrecognised value raises :class:`ValueError`,
    mirroring :func:`~helix_agent.runtime.secret_store.make_secret_store`.
    """
    valid: tuple[str, ...] = get_args(SandboxOciRuntime)
    if oci_runtime not in valid:
        msg = f"unknown sandbox OCI runtime: {oci_runtime!r} (expected one of {valid})"
        raise ValueError(msg)
    return SandboxRuntimeProvider(
        oci_runtime=oci_runtime,  # type: ignore[arg-type]  # validated above
        egress_network=egress_network,
    )
