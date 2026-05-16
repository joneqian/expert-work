"""Unit tests for :class:`SandboxRuntimeProvider` — Stream F.3 (test matrix #43).

Covers the Mini-ADR F-5 hardening flags landing in the ``docker run`` argv
and the dev (``runc``) vs prod (``runsc``) runtime split.
"""

from __future__ import annotations

import pytest

from helix_agent.runtime.sandbox import (
    DEFAULT_EGRESS_NETWORK,
    SandboxResourceLimits,
    SandboxRuntimeProvider,
    make_sandbox_runtime_provider,
)


def _flag_value(argv: list[str], flag: str) -> str:
    """Return the token immediately after ``flag`` in ``argv``."""
    return argv[argv.index(flag) + 1]


def _runc_provider() -> SandboxRuntimeProvider:
    return SandboxRuntimeProvider(oci_runtime="runc")


def _runsc_provider() -> SandboxRuntimeProvider:
    return SandboxRuntimeProvider(oci_runtime="runsc")


# ---------- hardening flags ----------


def test_argv_carries_all_hardening_flags() -> None:
    argv = _runc_provider().docker_run_argv(image="helix-sandbox:dev", container_name="sb-1")

    assert "--read-only" in argv
    assert _flag_value(argv, "--cap-drop") == "ALL"
    assert _flag_value(argv, "--security-opt") == "no-new-privileges"
    assert _flag_value(argv, "--pids-limit") == "128"
    assert _flag_value(argv, "--memory") == "512m"
    assert _flag_value(argv, "--cpus") == "1.0"
    assert _flag_value(argv, "--network") == DEFAULT_EGRESS_NETWORK
    assert _flag_value(argv, "--tmpfs") == "/workspace:rw,size=64m,mode=1777"


def test_argv_keeps_stdin_open_for_runner_protocol() -> None:
    argv = _runc_provider().docker_run_argv(image="img", container_name="sb-1")
    assert "--interactive" in argv


def test_argv_structure_name_then_image_last() -> None:
    argv = _runc_provider().docker_run_argv(image="helix-sandbox:dev", container_name="sb-7")
    assert argv[:2] == ["docker", "run"]
    assert _flag_value(argv, "--name") == "sb-7"
    assert argv[-1] == "helix-sandbox:dev"


# ---------- runc vs runsc split ----------


def test_runc_omits_runtime_flag() -> None:
    # runc is Docker's default — no --runtime flag is emitted.
    argv = _runc_provider().docker_run_argv(image="img", container_name="sb-1")
    assert "--runtime" not in argv


def test_runsc_appends_gvisor_runtime() -> None:
    argv = _runsc_provider().docker_run_argv(image="img", container_name="sb-1")
    assert _flag_value(argv, "--runtime") == "runsc"


# ---------- custom limits ----------


def test_custom_limits_reflected_in_argv() -> None:
    limits = SandboxResourceLimits(cpus=2.5, memory_mb=1024, pids_limit=64, workspace_size_mb=128)
    argv = _runc_provider().docker_run_argv(image="img", container_name="sb-1", limits=limits)
    assert _flag_value(argv, "--cpus") == "2.5"
    assert _flag_value(argv, "--memory") == "1024m"
    assert _flag_value(argv, "--pids-limit") == "64"
    assert _flag_value(argv, "--tmpfs") == "/workspace:rw,size=128m,mode=1777"


def test_custom_egress_network_reflected() -> None:
    provider = SandboxRuntimeProvider(oci_runtime="runc", egress_network="custom-net")
    argv = provider.docker_run_argv(image="img", container_name="sb-1")
    assert _flag_value(argv, "--network") == "custom-net"


# ---------- factory ----------


def test_factory_builds_provider_for_valid_runtime() -> None:
    assert make_sandbox_runtime_provider("runc").oci_runtime == "runc"
    assert make_sandbox_runtime_provider("runsc").oci_runtime == "runsc"


def test_factory_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match="unknown sandbox OCI runtime"):
        make_sandbox_runtime_provider("firecracker")
