"""CI lint: enforce helix harness compliance on agent manifests.

Stream SE (SE-15 / Mini-ADR SE-A34..A37). helix is a *declarative*
harness — an agent's seven orthogonal components (system rules / tool
descriptions / tool implementations / middleware / skills / sub-agents /
long-term memory) are declared in an ``AgentSpec`` manifest and assembled
into a ``BuiltAgent`` at runtime, rather than living as a file tree like
the workspace-style harnesses described in
``docs/HARNESS-COMPLIANCE.md``.

``AgentSpec`` already bakes eight ``model_validator`` rules (network
wildcard, fallback DAG, subagent / skill / trigger naming + dedup). This
lint adds the *cross-component / naming / compliance* rules those
per-block validators cannot express on their own:

* **R1 (error)** — every manifest parses as a valid ``AgentSpec``.
* **R2 (error)** — each declared ``builtin`` tool name is one the
  platform actually ships (``KNOWN_BUILTINS``). Guarded: if the
  orchestrator package is not importable (e.g. a minimal pre-commit
  env), the rule is *skipped with a printed notice* rather than
  silently passing — CI always has the full workspace synced so it runs
  there.
* **R3 (error)** — every enabled high-risk tool (``HIGH_RISK_TOOLS``)
  appears in ``policies.approval_required_tools``. Mirrors the Stream SE
  "high-risk always human-reviewed" hard guard at the manifest layer.
* **R4 (warn)** — ``metadata.name`` is lowercase-kebab
  (``[a-z][a-z0-9-]*``).
* **R5 (warn)** — ``system_prompt.template`` does not embed tool
  *implementation* detail (a fenced code block or an ``exec_python(``
  call) — that belongs in the tool layer, not the system-rules layer
  (orthogonality anti-pattern).
* **R6 (warn)** — a ``vision:`` block is not declared alongside a
  vision-capable ``model`` (the two J.6 paths are mutually exclusive;
  agent-build rejects it — the lint surfaces it earlier).
* **R-prof (warn)** — optional blocks the active compliance profile
  recommends for a production agent are present in the raw manifest.

Errors fail the build (exit 1); warnings are advisory (exit 0). Run via
``python tools/harness/check_harness_compliance.py`` from the repo root,
or pass a directory argument to scan a different tree (used by tests).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import yaml

# helix-protocol is pure-pydantic and always importable in the lint env.
from helix_agent.protocol.agent_spec import (
    AgentSpec,
    BuiltinToolSpec,
    HTTPToolSpec,
)
from helix_agent.protocol.skill import HIGH_RISK_TOOLS

#: Lowercase-kebab identifier — the convention agent ``metadata.name``
#: follows (e.g. ``canonical-agent``). Distinct from the snake_case
#: ``_SUBAGENT_NAME_RE`` because agent names are deploy slugs, not LLM
#: tool identifiers.
_AGENT_NAME_PATTERN: Final[str] = r"^[a-z][a-z0-9-]*$"

#: Default tree scanned. Agent manifests live under ``manifests/``; the
#: CI self-check proves every committed manifest is compliant.
_DEFAULT_ROOT: Final[str] = "manifests"

_PROFILE_PATH: Final[str] = "tools/harness/helix_profile.yaml"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _iter_yaml(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.yaml")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _load_doc(path: Path) -> dict[str, Any] | None:
    """Parse a YAML file; return the mapping only when it is an agent
    manifest (``kind: Agent``). Anything else is skipped (the tree may
    hold non-agent YAML)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(data, dict) and data.get("kind") == "Agent":
        return data
    return None


def _load_known_builtins() -> frozenset[str] | None:
    """Import the platform builtin-tool name set. Returns ``None`` when
    the orchestrator package is not importable so the caller can skip
    R2 with a notice instead of failing spuriously."""
    try:
        from orchestrator.tools.assembly import KNOWN_BUILTINS
    except Exception:  # pragma: no cover — env-dependent (heavy import)
        return None
    return frozenset(KNOWN_BUILTINS)


def _load_profile_recommend(repo_root: Path) -> tuple[str, ...]:
    """Optional spec blocks the active profile recommends (warn if absent)."""
    path = repo_root / _PROFILE_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    if not isinstance(data, dict):
        return ()
    recommend = data.get("recommend", [])
    if not isinstance(recommend, list):
        return ()
    return tuple(str(x) for x in recommend)


def _enabled_high_risk(spec: AgentSpec) -> set[str]:
    """High-risk tool names this manifest enables.

    ``http`` is enabled by an :class:`HTTPToolSpec` entry (no ``name``
    field — its dispatch name is the literal ``"http"``). Other
    high-risk names (e.g. ``exec_python``) arrive as builtin entries.
    """
    enabled: set[str] = set()
    for tool in spec.spec.tools:
        if isinstance(tool, HTTPToolSpec) and "http" in HIGH_RISK_TOOLS:
            enabled.add("http")
        elif isinstance(tool, BuiltinToolSpec) and tool.name in HIGH_RISK_TOOLS:
            enabled.add(tool.name)
    return enabled


def _check_manifest(
    raw: dict[str, Any],
    *,
    known_builtins: frozenset[str] | None,
    profile_recommend: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for one manifest mapping."""
    import re

    errors: list[str] = []
    warnings: list[str] = []

    # R1 — schema validity.
    try:
        spec = AgentSpec.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError or value errors
        errors.append(f"invalid AgentSpec: {exc}")
        return errors, warnings  # downstream rules need a valid spec

    body = spec.spec

    # R2 — builtin tool names known to the platform.
    if known_builtins is not None:
        for tool in body.tools:
            if isinstance(tool, BuiltinToolSpec) and tool.name not in known_builtins:
                errors.append(
                    f"unknown builtin tool {tool.name!r} (known: {sorted(known_builtins)})"
                )

    # R3 — enabled high-risk tools must be approval-gated.
    approval = set(body.policies.approval_required_tools)
    ungated = sorted(_enabled_high_risk(spec) - approval)
    for name in ungated:
        errors.append(
            f"high-risk tool {name!r} is enabled but not listed in "
            f"policies.approval_required_tools (Stream SE: high-risk "
            f"tools must be human-approved)"
        )

    # R4 — agent name convention.
    if not re.match(_AGENT_NAME_PATTERN, spec.metadata.name):
        warnings.append(
            f"metadata.name {spec.metadata.name!r} is not lowercase-kebab ({_AGENT_NAME_PATTERN})"
        )

    # R5 — system-rules / tool-impl orthogonality.
    template = body.system_prompt.template
    if "```" in template or "exec_python(" in template:
        warnings.append(
            "system_prompt.template embeds code (a fenced block or an "
            "exec_python(...) call) — tool implementation detail belongs "
            "in the tool layer, not the system-rules layer"
        )

    # R6 — vision block vs vision-capable model (mutually exclusive).
    if body.vision is not None and body.model.supports_vision:
        warnings.append(
            "vision: block declared while model.supports_vision is true — "
            "the two J.6 paths are mutually exclusive (agent-build rejects this)"
        )

    # R-prof — recommended optional blocks present in the raw manifest.
    raw_spec = raw.get("spec", {})
    raw_keys = set(raw_spec) if isinstance(raw_spec, dict) else set()
    for block in profile_recommend:
        if block not in raw_keys:
            warnings.append(f"profile recommends declaring spec.{block} for a production agent")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    repo_root = _repo_root()
    root = Path(args[0]) if args else repo_root / _DEFAULT_ROOT

    known_builtins = _load_known_builtins()
    profile_recommend = _load_profile_recommend(repo_root)

    total_errors = 0
    total_warnings = 0
    n_manifests = 0

    if known_builtins is None:
        print(
            "NOTICE — orchestrator not importable; skipping builtin-name "
            "rule R2 (still enforced in CI where the workspace is synced)."
        )

    for path in _iter_yaml(root):
        raw = _load_doc(path)
        if raw is None:
            continue
        n_manifests += 1
        errors, warnings = _check_manifest(
            raw,
            known_builtins=known_builtins,
            profile_recommend=profile_recommend,
        )
        rel = path.relative_to(repo_root) if path.is_relative_to(repo_root) else path
        for msg in errors:
            print(f"ERROR  {rel}: {msg}", file=sys.stderr)
        for msg in warnings:
            print(f"WARN   {rel}: {msg}")
        total_errors += len(errors)
        total_warnings += len(warnings)

    if total_errors:
        print(
            f"FAIL — {total_errors} error(s), {total_warnings} warning(s) "
            f"across {n_manifests} manifest(s)",
            file=sys.stderr,
        )
        return 1

    print(f"OK — {n_manifests} manifest(s) compliant ({total_warnings} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
