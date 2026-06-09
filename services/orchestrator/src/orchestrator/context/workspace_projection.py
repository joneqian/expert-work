"""Stream CM-0 — project authoritative agent state into workspace files.

The DB (LangGraph checkpoint + ``memory_item`` rows) is the **source of
truth**; these Markdown files are a *projection* of it into the per-user
``/workspace`` so the agent can ``read_file`` them and a human can open them
to see — and steer — the run. State flows one direction at a time
(Mini-ADR CM-A2): this module renders + writes ``DB → file`` at a turn
boundary; the inverse ``file → DB`` controlled ingest is a separate path.

**Pure core (this module)** — rendering is pure functions and
:class:`WorkspaceProjector` writes through an injected
:class:`WorkspaceFileWriter` seam, so the only-if-changed digest gate and
the best-effort contract are unit-testable without a live sandbox. The real
writer (a ``write_file`` snippet in the J.15 warm sandbox — the only channel
with workspace-volume write access, see CM-A1) is wired separately.

**Best-effort (Mini-ADR CM-A8)** — a failing write is logged and never
propagates (projection must never break a run), and the returned digest does
*not* advance past a partial failure so the next turn retries.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from helix_agent.protocol import MemoryItem, Plan

logger = logging.getLogger(__name__)

#: Projected file names at the workspace root.
PLAN_FILE = "PLAN.md"
TODO_FILE = "TODO.md"
MEMORY_FILE = "MEMORY.md"

_PROJECTION_NOTE = (
    "<!-- Projected from agent state (DB is the source of truth). "
    "Edits here are ingested at the next run start. -->"
)
_MEMORY_NOTE = "<!-- Read-only projection of long-term memory. -->"


# ---------------------------------------------------------------------------
# Render functions (pure)
# ---------------------------------------------------------------------------


def render_plan_md(plan: Plan | None) -> str:
    """Render ``PLAN.md`` (goal + ordered steps). ``None`` → empty string
    (no plan to project, e.g. a plain ReAct run)."""
    if plan is None:
        return ""
    lines = [_PROJECTION_NOTE, "", "# Plan", "", plan.goal, "", "## Steps", ""]
    lines.extend(f"{step.id}. {step.description}" for step in plan.steps)
    return "\n".join(lines) + "\n"


def render_todo_md(plan: Plan | None) -> str:
    """Render ``TODO.md`` as a checkbox list driven by each step's status:
    ``completed`` → ``[x]``; ``in_progress`` → ``[ ]`` with an explicit
    marker; ``pending`` → ``[ ]``. ``None`` → empty string."""
    if plan is None:
        return ""
    lines = [_PROJECTION_NOTE, "", "# TODO", ""]
    for step in plan.steps:
        box = "x" if step.status == "completed" else " "
        suffix = " — in progress" if step.status == "in_progress" else ""
        lines.append(f"- [{box}] {step.id}. {step.description}{suffix}")
    return "\n".join(lines) + "\n"


def render_memory_md(memories: Sequence[MemoryItem]) -> str:
    """Render ``MEMORY.md`` — a read-only digest of recalled / consolidated
    long-term memories. Empty sequence → empty string."""
    if not memories:
        return ""
    lines = [_MEMORY_NOTE, "", "# Memory", ""]
    lines.extend(f"- ({item.kind}) {item.content}" for item in memories)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Projector
# ---------------------------------------------------------------------------


class WorkspaceFileWriter(Protocol):
    """Writes one workspace file. The real implementation rides the warm
    sandbox ``write_file`` snippet (CM-A1); tests inject a fake."""

    async def write(self, *, rel: str, content: str) -> None: ...


@dataclass(frozen=True)
class ProjectionResult:
    """Outcome of one :meth:`WorkspaceProjector.project` call.

    ``digest`` is the new content digest to persist as the projection cursor
    when every intended write succeeded; on a partial failure it stays at the
    caller's ``last_digest`` so the next turn retries. ``None`` means there
    was nothing to project.
    """

    written: tuple[str, ...]
    skipped: bool
    digest: str | None


def _digest(items: Sequence[tuple[str, str]]) -> str | None:
    """Content digest over the (path, content) pairs about to be projected.
    ``None`` when there is nothing to project."""
    if not items:
        return None
    hasher = hashlib.sha256()
    for rel, content in items:
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(content.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


@dataclass(frozen=True)
class WorkspaceProjector:
    """Projects agent state into workspace files through a writer seam."""

    writer: WorkspaceFileWriter

    async def project(
        self,
        *,
        plan: Plan | None,
        memories: Sequence[MemoryItem],
        last_digest: str | None,
    ) -> ProjectionResult:
        """Render the projected files, and — only when their content changed
        since ``last_digest`` — write each through the seam. Best-effort: a
        failing write is logged, excluded from ``written``, and prevents the
        digest from advancing (so the next turn retries) but never raises."""
        items: list[tuple[str, str]] = []
        plan_md = render_plan_md(plan)
        if plan_md:
            items.append((PLAN_FILE, plan_md))
        todo_md = render_todo_md(plan)
        if todo_md:
            items.append((TODO_FILE, todo_md))
        memory_md = render_memory_md(memories)
        if memory_md:
            items.append((MEMORY_FILE, memory_md))

        digest = _digest(items)
        if digest == last_digest:
            logger.debug("workspace_projection.unchanged")
            return ProjectionResult(written=(), skipped=True, digest=digest)

        written: list[str] = []
        all_ok = True
        for rel, content in items:
            try:
                await self.writer.write(rel=rel, content=content)
            # Best-effort (CM-A8): projection must never break a run. ``Exception``
            # (not ``BaseException``) so ``asyncio.CancelledError`` still propagates.
            except Exception:
                all_ok = False
                logger.warning(
                    "workspace_projection.write_failed", extra={"rel": rel}, exc_info=True
                )
                continue
            written.append(rel)

        out_digest = digest if all_ok else last_digest
        logger.info(
            "workspace_projection.projected",
            extra={"written": written, "all_ok": all_ok},
        )
        return ProjectionResult(written=tuple(written), skipped=False, digest=out_digest)
