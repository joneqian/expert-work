"""Runner internals for ``run_longmem.py`` — Stream CM-N5.

Kept separate from the CLI entry so the resume / merge / baseline
plumbing is unit-testable in CI without invoking argparse or any
network path.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from longmem.endtoend import QAResult
from longmem.retrieval import AblationConfig

#: The CM-K5 matrix — named arms so baseline rows are self-describing.
#: ``default`` is the production shape; every other arm flips one switch.
ARMS: dict[str, AblationConfig] = {
    "default": AblationConfig(),
    "vector": AblationConfig(hybrid=False),
    "no_decay": AblationConfig(decay=False),
    "no_mmr": AblationConfig(mmr=False),
    "rerank": AblationConfig(rerank=True),
}


def resolve_arms(spec: str) -> dict[str, AblationConfig]:
    """Parse ``--arms`` (comma list or ``all``) against the registry."""
    names = list(ARMS) if spec == "all" else [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s): {', '.join(unknown)} (known: {', '.join(ARMS)})")
    return {name: ARMS[name] for name in names}


# ---------------------------------------------------------------------------
# end-to-end resume — jsonl, append-only
# ---------------------------------------------------------------------------


def load_results(path: Path) -> list[QAResult]:
    """Read prior verdicts; a torn trailing line (killed mid-write) is skipped."""
    if not path.exists():
        return []
    results: list[QAResult] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            results.append(
                QAResult(
                    question_id=str(row["question_id"]),
                    question_type=str(row["question_type"]),
                    hypothesis=str(row["hypothesis"]),
                    correct=bool(row["correct"]),
                    n_memories=int(row["n_memories"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return results


def append_result(path: Path, result: QAResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "question_id": result.question_id,
                    "question_type": result.question_type,
                    "hypothesis": result.hypothesis,
                    "correct": result.correct,
                    "n_memories": result.n_memories,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def merge_results(prior: list[QAResult], fresh: list[QAResult]) -> list[QAResult]:
    """Fresh verdicts win on question_id collisions (re-run overrides)."""
    by_id = {r.question_id: r for r in prior}
    for result in fresh:
        by_id[result.question_id] = result
    return list(by_id.values())


def summarise(results: list[QAResult]) -> dict[str, Any]:
    """Accuracy + per-type breakdown over a merged result set."""
    by_type: dict[str, list[QAResult]] = defaultdict(list)
    for result in results:
        by_type[result.question_type].append(result)
    return {
        "n_instances": len(results),
        "accuracy": (sum(1 for r in results if r.correct) / len(results)) if results else 0.0,
        "by_question_type": {
            qtype: {
                "n": float(len(rows)),
                "accuracy": sum(1 for r in rows if r.correct) / len(rows),
            }
            for qtype, rows in sorted(by_type.items())
        },
    }


# ---------------------------------------------------------------------------
# baseline file — one YAML, sections merged across runs
# ---------------------------------------------------------------------------


def update_baseline(
    path: Path,
    *,
    tier: str,
    benchmark: str,
    section: dict[str, Any],
    fingerprint: dict[str, Any],
) -> None:
    """Merge one run's numbers into the baseline YAML.

    Layout: ``{tier: {benchmark: section}}`` plus a ``meta.fingerprint``
    per (tier, benchmark) so every number carries its config provenance
    (CM-K6 — numbers without their judge/embedder/commit are the exact
    vendor-table mistake this stream exists to avoid).
    """
    data: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    data.setdefault(tier, {})[benchmark] = section
    meta = data.setdefault("meta", {})
    meta.setdefault("fingerprints", {})[f"{tier}/{benchmark}"] = fingerprint
    meta["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=True, width=100), encoding="utf-8"
    )


def limit_instances(instances: list[Any], limit: int) -> list[Any]:
    """Head-cap for smoke runs; ``0`` = the full set (the CM-K2 default)."""
    return instances if limit <= 0 else instances[:limit]


def with_top_k(config: AblationConfig, top_k: int | None) -> AblationConfig:
    return config if top_k is None else replace(config, top_k=top_k)
