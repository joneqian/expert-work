"""Manifest loader tests — YAML + Jinja2 + Pydantic lint pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from control_plane.manifest import (
    ManifestLoader,
    ManifestSyntaxError,
    ManifestTemplateError,
    ManifestValidationError,
    load_manifest,
)

_MINIMAL_TEMPLATE = """\
apiVersion: expert_work.io/v1
kind: Agent
metadata:
  name: "{{ name }}"
  version: "1.0.0"
  tenant: platform-eng
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-5
  system_prompt:
    template: "you are a reviewer"
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: ["api.anthropic.com"]
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
"""


def _rendered_minimal() -> str:
    return _MINIMAL_TEMPLATE.replace("{{ name }}", "code-reviewer")


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_load_minimal_yaml() -> None:
    spec = load_manifest(_rendered_minimal())
    assert spec.metadata.name == "code-reviewer"
    assert spec.spec.model.provider == "anthropic"


def test_template_variable_substituted() -> None:
    spec = load_manifest(_MINIMAL_TEMPLATE, template_vars={"name": "router-agent"})
    assert spec.metadata.name == "router-agent"


def test_load_from_path(tmp_path: Path) -> None:
    f = tmp_path / "manifest.yaml"
    f.write_text(_rendered_minimal(), encoding="utf-8")
    spec = load_manifest(f)
    assert spec.metadata.name == "code-reviewer"


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_size_cap_enforced() -> None:
    loader = ManifestLoader(max_size_bytes=512)
    huge = "x" * 1024
    with pytest.raises(ManifestSyntaxError) as exc_info:
        loader.load_from_string(huge)
    assert "size cap" in str(exc_info.value)


def test_undefined_template_var_raises() -> None:
    with pytest.raises(ManifestTemplateError):
        load_manifest(_MINIMAL_TEMPLATE)


def test_broken_yaml_raises_syntax() -> None:
    with pytest.raises(ManifestSyntaxError):
        load_manifest("apiVersion: expert_work.io/v1\nkind: Agent\nthis: is: broken: yaml")


def test_non_mapping_root_raises_syntax() -> None:
    with pytest.raises(ManifestSyntaxError):
        load_manifest("- just-a-list")


def test_pydantic_validation_error_surfaces() -> None:
    """Missing required ``kind`` → ManifestValidationError with the
    underlying pydantic errors attached."""
    broken = _rendered_minimal().replace("kind: Agent\n", "")
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(broken)
    assert exc_info.value.errors  # non-empty list of pydantic errors
    assert any("kind" in str(err.get("loc", "")) for err in exc_info.value.errors)


def test_lint_wildcard_allowlist_rejected() -> None:
    broken = _rendered_minimal().replace(
        'allowlist: ["api.anthropic.com"]',
        'allowlist: ["*"]',
    )
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(broken)
    # The summary message is intentionally generic (CodeQL stack-trace
    # exposure). Detail lives on ``.errors`` as a curated whitelist.
    assert any("allowlist" in str(err["msg"]).lower() for err in exc_info.value.errors)


def test_lint_fallback_cycle_rejected() -> None:
    """Self-referential fallback chain trips lint rule #8."""
    cycle_fragment = (
        "name: claude-sonnet-4-5\n"
        "    fallback:\n"
        "      - { provider: anthropic, name: claude-sonnet-4-5 }"
    )
    broken = _rendered_minimal().replace("name: claude-sonnet-4-5", cycle_fragment)
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(broken)
    assert any("cycle" in str(err["msg"]).lower() for err in exc_info.value.errors)


def test_loader_rejects_non_positive_size_cap() -> None:
    with pytest.raises(ValueError):
        ManifestLoader(max_size_bytes=0)


def test_yaml_safe_load_blocks_arbitrary_objects() -> None:
    """``yaml.safe_load`` must refuse the Python-tagged construction
    vector used by CVE-2017-18342 / 2020-1747."""
    malicious = """\
!!python/object/apply:os.system
- "echo pwned"
"""
    with pytest.raises(ManifestSyntaxError):
        load_manifest(malicious)
