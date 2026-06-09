"""Unit tests for the harness-compliance lint (Stream SE / SE-15).

The script lives in the flat ``tools/`` tree (not a Python package), so
it is loaded via :mod:`importlib.util` — same pattern as the other
``tools/*`` lint tests. Rule-level assertions call ``_check_manifest``
directly with an explicit ``known_builtins`` set so they never depend on
the heavy ``orchestrator`` import; the end-to-end cases drive ``main``.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE / "check_harness_compliance.py"

_KNOWN = frozenset({"web_search", "exec_python", "bash"})
_RECOMMEND = ("observability",)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_harness_compliance", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        msg = f"failed to load script at {_SCRIPT}"
        raise RuntimeError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _base_manifest() -> dict[str, Any]:
    """A minimal valid AgentSpec mapping."""
    return {
        "apiVersion": "helix.io/v1",
        "kind": "Agent",
        "metadata": {"name": "demo-agent", "version": "1.0.0", "tenant": "acme"},
        "spec": {
            "tenant_config": {},
            "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
            "system_prompt": {"template": "You are a helpful agent."},
            "sandbox": {
                "resources": {"cpu": "1.0", "memory": "1Gi"},
                "network": {},
                "filesystem": {},
            },
            "observability": {"log_level": "info"},
        },
    }


def _check(raw: dict[str, Any]) -> tuple[list[str], list[str]]:
    return mod._check_manifest(raw, known_builtins=_KNOWN, profile_recommend=_RECOMMEND)


# --- rule-level -----------------------------------------------------------


def test_clean_manifest_has_no_findings() -> None:
    errors, warnings = _check(_base_manifest())
    assert errors == []
    assert warnings == []


def test_invalid_schema_is_an_error() -> None:
    raw = _base_manifest()
    del raw["spec"]["model"]
    errors, _ = _check(raw)
    assert len(errors) == 1
    assert "invalid AgentSpec" in errors[0]


def test_unknown_builtin_is_an_error() -> None:
    raw = _base_manifest()
    raw["spec"]["tools"] = [{"type": "builtin", "name": "bogus_tool"}]
    errors, _ = _check(raw)
    assert any("bogus_tool" in e for e in errors)


def test_known_builtin_passes() -> None:
    raw = _base_manifest()
    raw["spec"]["tools"] = [{"type": "builtin", "name": "web_search"}]
    errors, _ = _check(raw)
    assert errors == []


def test_high_risk_http_without_approval_is_an_error() -> None:
    raw = _base_manifest()
    raw["spec"]["tools"] = [{"type": "http"}]
    errors, _ = _check(raw)
    assert any("http" in e and "approval" in e for e in errors)


def test_high_risk_http_with_approval_passes() -> None:
    raw = _base_manifest()
    raw["spec"]["tools"] = [{"type": "http"}]
    raw["spec"]["policies"] = {"approval_required_tools": ["http"]}
    errors, _ = _check(raw)
    assert errors == []


def test_high_risk_exec_python_without_approval_is_an_error() -> None:
    raw = _base_manifest()
    raw["spec"]["tools"] = [{"type": "builtin", "name": "exec_python"}]
    errors, _ = _check(raw)
    assert any("exec_python" in e and "approval" in e for e in errors)


def test_bad_agent_name_is_a_warning() -> None:
    raw = _base_manifest()
    raw["metadata"]["name"] = "Bad_Name"
    errors, warnings = _check(raw)
    assert errors == []
    assert any("lowercase-kebab" in w for w in warnings)


def test_code_in_system_prompt_is_a_warning() -> None:
    raw = _base_manifest()
    raw["spec"]["system_prompt"]["template"] = "Run this:\n```\nprint(1)\n```"
    errors, warnings = _check(raw)
    assert errors == []
    assert any("system_prompt.template embeds code" in w for w in warnings)


def test_vision_block_with_vision_model_is_a_warning() -> None:
    raw = _base_manifest()
    raw["spec"]["model"]["supports_vision"] = True
    raw["spec"]["vision"] = {"model": {"provider": "anthropic", "name": "claude-sonnet-4-5"}}
    errors, warnings = _check(raw)
    assert errors == []
    assert any("mutually exclusive" in w for w in warnings)


def test_missing_recommended_block_is_a_warning() -> None:
    raw = _base_manifest()
    del raw["spec"]["observability"]
    errors, warnings = _check(raw)
    assert errors == []
    assert any("spec.observability" in w for w in warnings)


# --- end-to-end via main --------------------------------------------------


def _write(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_main_clean_tree_returns_zero(tmp_path: Path) -> None:
    _write(tmp_path / "a.yaml", _base_manifest())
    # A non-agent YAML in the tree is ignored.
    (tmp_path / "other.yaml").write_text("foo: bar\n", encoding="utf-8")
    assert mod.main([str(tmp_path)]) == 0


def test_main_schema_error_returns_one(tmp_path: Path) -> None:
    bad = _base_manifest()
    del bad["spec"]["system_prompt"]
    _write(tmp_path / "bad.yaml", bad)
    assert mod.main([str(tmp_path)]) == 1


def test_main_empty_tree_returns_zero(tmp_path: Path) -> None:
    assert mod.main([str(tmp_path)]) == 0


def test_canonical_manifest_is_compliant() -> None:
    """The committed canonical manifest must pass the lint (no drift)."""
    repo_root = Path(__file__).resolve().parents[2]
    canonical = repo_root / "manifests" / "canonical-agent" / "v1.0.0.yaml"
    raw = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    errors, _ = _check(copy.deepcopy(raw))
    assert errors == []
