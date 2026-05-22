"""Export curated ``eval_dataset`` rows to a J.13 YAML file — Stream J.12.

J.12 produces curated rows in the ``eval_dataset`` PG table; J.13
consumes checked-in YAML datasets (Mini-ADR J-38). This CLI bridges the
two — the ``eval_dataset`` rows for one ``(tenant, agent, name)`` are
rendered to a YAML file under ``tools/eval/datasets/``; the operator
reviews the diff and commits it, keeping the checked-in YAML the Gate
artifact.

Usage::

    .venv/bin/python tools/eval/export_dataset.py \\
        --dsn postgresql+asyncpg://... --tenant <uuid> \\
        --agent reporter --name regression-set \\
        --out tools/eval/datasets/reporter/curated.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

from helix_agent.persistence import (
    DatabaseConfig,
    SqlEvalDatasetStore,
    create_async_engine_from_config,
    create_async_session_factory,
)
from helix_agent.persistence.rls import build_rls_sessionmaker, current_tenant_id_var
from helix_agent.protocol import EvalDatasetRecord


def render_dataset_yaml(records: Iterable[EvalDatasetRecord]) -> str:
    """Render curated rows to the J.13 case-list YAML shape.

    One ``eval_dataset`` row → one ``cases`` entry carrying its
    ``id`` / ``source`` / ``input`` / ``expected``. Rows are emitted in
    ``created_at`` order so the file is a stable, reviewable diff.
    """
    cases: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda r: r.created_at):
        cases.append(
            {
                "id": str(record.id),
                "source": record.source,
                "input": dict(record.input),
                "expected": dict(record.expected) if record.expected is not None else None,
            }
        )
    return yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True)


async def _export(*, dsn: str, tenant_id: UUID, agent_name: str, name: str) -> str:
    """Query the curated rows for one ``(tenant, agent, name)`` and render them."""
    engine = create_async_engine_from_config(DatabaseConfig(dsn=dsn))
    try:
        store = SqlEvalDatasetStore(build_rls_sessionmaker(create_async_session_factory(engine)))
        token = current_tenant_id_var.set(tenant_id)
        try:
            rows = await store.list_by_agent(tenant_id=tenant_id, agent_name=agent_name)
        finally:
            current_tenant_id_var.reset(token)
    finally:
        await engine.dispose()
    return render_dataset_yaml(r for r in rows if r.name == name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export curated eval_dataset rows to YAML.")
    parser.add_argument("--dsn", required=True, help="async Postgres DSN")
    parser.add_argument("--tenant", required=True, type=UUID, help="tenant id")
    parser.add_argument("--agent", required=True, help="agent_name to export")
    parser.add_argument("--name", required=True, help="dataset name to export")
    parser.add_argument("--out", required=True, type=Path, help="output YAML path")
    args = parser.parse_args(argv)

    yaml_text = asyncio.run(
        _export(dsn=args.dsn, tenant_id=args.tenant, agent_name=args.agent, name=args.name)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(yaml_text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
