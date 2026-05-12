"""CI lint: scan source for raw ``Counter`` / ``Histogram`` / ``Gauge`` usage.

Per subsystems/20-observability § 6:

    "高基数 label 爆 Prometheus → label cardinality 检查 CI gate
    (regex 检查 metric definition)"

The :mod:`helix_agent.common.observability.metrics` module already does
the validation at construction time, but only **if** call sites import
``helix_counter`` etc. This lint catches the path where someone reaches
past the wrapper and imports ``Counter`` directly from ``prometheus_client``,
which would silently bypass the naming + label rules.

Run via ``python -m tools.observability.check_metric_names`` from the
repo root, or directly. Exits 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

_BANNED_NAMES = frozenset({"Counter", "Histogram", "Gauge", "Summary"})
_WRAPPER_MODULE = "helix_agent.common.observability.metrics"

# Files allowed to import the raw prometheus_client constructors — the
# wrapper module itself and its tests need to reach the real class.
_ALLOWLIST_SUFFIXES = (
    "/helix-common/src/helix_agent/common/observability/metrics.py",
    "/helix-common/tests/test_observability_metrics.py",
    "/tools/observability/check_metric_names.py",
)


def _is_allowed(path: Path) -> bool:
    posix = path.as_posix()
    return any(posix.endswith(suffix) for suffix in _ALLOWLIST_SUFFIXES)


def _iter_python_sources(root: Path) -> Iterator[Path]:
    for path in root.rglob("*.py"):
        if "/.venv/" in path.as_posix() or "/dist/" in path.as_posix():
            continue
        if "/build/" in path.as_posix() or "/__pycache__/" in path.as_posix():
            continue
        yield path


def _find_violations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: failed to parse: {exc}"]

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "prometheus_client":
            for alias in node.names:
                if alias.name in _BANNED_NAMES:
                    violations.append(
                        f"{path}:{node.lineno}: imports prometheus_client.{alias.name} "
                        f"directly; use helix_counter / helix_histogram / helix_gauge "
                        f"from {_WRAPPER_MODULE} instead."
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path.cwd()

    all_violations: list[str] = []
    for path in _iter_python_sources(root):
        if _is_allowed(path):
            continue
        all_violations.extend(_find_violations(path))

    if all_violations:
        print("Metric naming lint failed:", file=sys.stderr)
        for v in all_violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print("Metric naming lint OK.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
