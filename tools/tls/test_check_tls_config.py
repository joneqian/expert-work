"""Unit tests for the TLS config lint script.

The script lives outside any Python package (``tools/`` is a flat
directory of operational scripts, not a Python package). We load it via
:mod:`importlib.util` rather than rely on ``tools.*`` being on sys.path.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE / "check_tls_config.py"


def _load_main() -> Callable[[list[str] | None], int]:
    spec = importlib.util.spec_from_file_location("check_tls_config", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        msg = f"failed to load script at {_SCRIPT}"
        raise RuntimeError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn: Callable[[list[str] | None], int] = mod.main
    return fn


main = _load_main()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_clean_when_all_envs_meet_minimum(tmp_path: Path) -> None:
    _write(tmp_path / "environments/dev.yaml", "environment: dev\ntls:\n  min_version: 1.2\n")
    _write(tmp_path / "environments/prod.yaml", "environment: prod\ntls:\n  min_version: 1.3\n")
    assert main([str(tmp_path)]) == 0


def test_clean_when_tls_block_absent(tmp_path: Path) -> None:
    """``tls`` block is optional in early-M0 envs; the lint only kicks in
    once the block exists. Until then, anything's allowed (Stream B / cert
    rollout flips it to required)."""
    _write(tmp_path / "environments/dev.yaml", "environment: dev\n")
    assert main([str(tmp_path)]) == 0


def test_fails_when_min_version_below_floor(tmp_path: Path) -> None:
    _write(tmp_path / "environments/dev.yaml", "environment: dev\ntls:\n  min_version: 1.0\n")
    assert main([str(tmp_path)]) == 1


def test_fails_when_min_version_missing_from_populated_block(tmp_path: Path) -> None:
    _write(
        tmp_path / "environments/dev.yaml",
        "environment: dev\ntls:\n  ca_bundle: /etc/ca.crt\n",
    )
    assert main([str(tmp_path)]) == 1


def test_fails_when_min_version_unparseable(tmp_path: Path) -> None:
    _write(
        tmp_path / "environments/dev.yaml",
        "environment: dev\ntls:\n  min_version: yes-please\n",
    )
    assert main([str(tmp_path)]) == 1


def test_aggregates_errors_across_files(tmp_path: Path) -> None:
    _write(tmp_path / "environments/dev.yaml", "environment: dev\ntls:\n  min_version: 1.0\n")
    _write(
        tmp_path / "environments/staging.yaml",
        "environment: staging\ntls:\n  min_version: 1.1\n",
    )
    assert main([str(tmp_path)]) == 1


def test_clean_when_no_env_files(tmp_path: Path) -> None:
    # No ``environments/`` directory at all → vacuously clean.
    assert main([str(tmp_path)]) == 0


def test_real_repo_is_clean() -> None:
    """Smoke-check the *actual* repo environments: dev / staging / prod
    all declare a compliant ``tls`` block (Stream A.10 deliverable)."""
    repo_root = _HERE.parents[1]
    assert main([str(repo_root)]) == 0


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
