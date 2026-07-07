"""Stream L.L6 — :mod:`orchestrator.tools.scheduling` unit tests.

Covers the conflict rules in :func:`conflicts` and the greedy stage
planner :func:`plan_stages`. The end-to-end ``tools_node`` integration
(parallel dispatch + ``expert_work_tools_batch_concurrency``) lives in
:mod:`test_react_graph_parallel`.
"""

from __future__ import annotations

from orchestrator.tools.registry import SideEffectLevel, ToolSpec
from orchestrator.tools.scheduling import (
    MAX_TOOL_WORKERS,
    _resolve_paths,
    _ScheduledCall,
    conflicts,
    plan_stages,
)


def _spec(
    name: str,
    *,
    read_only: bool = False,
    path_args: tuple[str, ...] = (),
    side_effect: SideEffectLevel | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"scripted {name}",
        is_read_only=read_only,
        path_args=path_args,
        side_effect=side_effect,
    )


def _call(
    index: int,
    name: str,
    *,
    args: dict[str, object] | None = None,
    spec: ToolSpec | None = None,
) -> _ScheduledCall:
    args = args or {}
    return _ScheduledCall(
        index=index,
        name=name,
        args=args,
        spec=spec,
        paths=_resolve_paths(spec, args),
    )


# ---------------------------------------------------------------------------
# Conflict matrix
# ---------------------------------------------------------------------------


def test_two_reads_never_conflict_even_on_same_path() -> None:
    """Pure reads can race safely — no torn writes possible. Same path
    is fine because neither call mutates it."""
    s = _spec("read", read_only=True, path_args=("p",))
    a = _call(0, "read", args={"p": "x"}, spec=s)
    b = _call(1, "read", args={"p": "x"}, spec=s)
    assert not conflicts(a, b)
    assert not conflicts(b, a)


def test_two_writes_with_disjoint_paths_do_not_conflict() -> None:
    """``save_artifact``-style writes on different file names can run
    in parallel — the agent can register two separate artifacts at
    once."""
    s = _spec("write", path_args=("p",))
    a = _call(0, "write", args={"p": "a.md"}, spec=s)
    b = _call(1, "write", args={"p": "b.md"}, spec=s)
    assert not conflicts(a, b)


def test_two_writes_with_overlapping_paths_conflict() -> None:
    s = _spec("write", path_args=("p",))
    a = _call(0, "write", args={"p": "report.md"}, spec=s)
    b = _call(1, "write", args={"p": "report.md"}, spec=s)
    assert conflicts(a, b)


def test_read_and_write_on_same_path_conflict() -> None:
    """A read might see the partial state of a concurrent write — the
    scheduler serialises them."""
    reader = _spec("read", read_only=True, path_args=("p",))
    writer = _spec("write", path_args=("p",))
    a = _call(0, "read", args={"p": "f"}, spec=reader)
    b = _call(1, "write", args={"p": "f"}, spec=writer)
    assert conflicts(a, b)


def test_read_and_write_with_disjoint_paths_do_not_conflict() -> None:
    reader = _spec("read", read_only=True, path_args=("p",))
    writer = _spec("write", path_args=("p",))
    a = _call(0, "read", args={"p": "a"}, spec=reader)
    b = _call(1, "write", args={"p": "b"}, spec=writer)
    assert not conflicts(a, b)


def test_write_with_empty_path_args_conflicts_with_everything() -> None:
    """``update_plan`` / ``subagent`` write shared state (AgentState,
    sandbox); without declared paths the conservative rule serialises
    them against every other call."""
    write = _spec("update_plan")
    read = _spec("read", read_only=True, path_args=("p",))
    plan_call = _call(0, "update_plan", spec=write)
    read_call = _call(1, "read", args={"p": "x"}, spec=read)
    assert conflicts(plan_call, read_call)
    assert conflicts(read_call, plan_call)


def test_unknown_tool_treated_as_conservative_write() -> None:
    """If the registry doesn't know the tool name (typo, missing
    registration), the scheduler treats it as ``spec=None`` → empty
    paths + non-read-only → conflicts with everything."""
    a = _call(0, "unknown")  # spec=None
    b = _call(1, "knowledge_search", spec=_spec("knowledge_search", read_only=True))
    assert conflicts(a, b)


def test_normalises_relative_path_prefix() -> None:
    """``./report.md`` and ``report.md`` resolve to the same path —
    callers shouldn't have to remember to strip the leading ``./``."""
    s = _spec("write", path_args=("p",))
    a = _call(0, "write", args={"p": "./report.md"}, spec=s)
    b = _call(1, "write", args={"p": "report.md"}, spec=s)
    assert conflicts(a, b)


# ---------------------------------------------------------------------------
# plan_stages — greedy assignment
# ---------------------------------------------------------------------------


def test_three_independent_reads_become_one_stage() -> None:
    """The whole point of L6: parallel-safe reads collapse into one
    stage so they run concurrently via ``asyncio.gather``."""
    knowledge = _spec("knowledge_search", read_only=True)
    web = _spec("web_search", read_only=True)
    tool_calls = [
        {"name": "knowledge_search", "args": {"query": "a"}, "id": "tc-0"},
        {"name": "web_search", "args": {"query": "b"}, "id": "tc-1"},
        {"name": "knowledge_search", "args": {"query": "c"}, "id": "tc-2"},
    ]
    stages = plan_stages(
        tool_calls,
        {"knowledge_search": knowledge, "web_search": web},
    )
    assert len(stages) == 1
    assert [c.index for c in stages[0]] == [0, 1, 2]


def test_writes_to_same_path_serialise_into_separate_stages() -> None:
    """Two saves to the same artifact name must run one-after-another
    so version counters stay consistent."""
    save = _spec("save_artifact", path_args=("name",))
    tool_calls = [
        {"name": "save_artifact", "args": {"name": "report.md"}, "id": "tc-0"},
        {"name": "save_artifact", "args": {"name": "report.md"}, "id": "tc-1"},
    ]
    stages = plan_stages(tool_calls, {"save_artifact": save})
    assert len(stages) == 2
    assert [c.index for c in stages[0]] == [0]
    assert [c.index for c in stages[1]] == [1]


def test_writes_to_different_paths_share_one_stage() -> None:
    save = _spec("save_artifact", path_args=("name",))
    tool_calls = [
        {"name": "save_artifact", "args": {"name": "a.md"}, "id": "tc-0"},
        {"name": "save_artifact", "args": {"name": "b.md"}, "id": "tc-1"},
    ]
    stages = plan_stages(tool_calls, {"save_artifact": save})
    assert len(stages) == 1
    assert [c.index for c in stages[0]] == [0, 1]


def test_update_plan_alone_then_reads_in_next_stage() -> None:
    """``update_plan`` (write, no path) conflicts with everything → its
    own stage. Subsequent reads cluster into the next stage."""
    update_plan = _spec("update_plan")
    web = _spec("web_search", read_only=True)
    tool_calls = [
        {"name": "update_plan", "args": {"steps": ["x"], "reason": "y"}, "id": "tc-0"},
        {"name": "web_search", "args": {"query": "a"}, "id": "tc-1"},
        {"name": "web_search", "args": {"query": "b"}, "id": "tc-2"},
    ]
    stages = plan_stages(
        tool_calls,
        {"update_plan": update_plan, "web_search": web},
    )
    assert len(stages) == 2
    assert [c.index for c in stages[0]] == [0]
    assert [c.index for c in stages[1]] == [1, 2]


def test_greedy_finds_earliest_compatible_stage() -> None:
    """A later read-only call should slot into the first stage if it
    has no conflict — minimising total stage count keeps end-to-end
    latency down."""
    web = _spec("web_search", read_only=True)
    save = _spec("save_artifact", path_args=("name",))
    tool_calls = [
        {"name": "web_search", "args": {"query": "a"}, "id": "tc-0"},
        {"name": "save_artifact", "args": {"name": "x.md"}, "id": "tc-1"},
        {"name": "web_search", "args": {"query": "b"}, "id": "tc-2"},
    ]
    stages = plan_stages(
        tool_calls,
        {"web_search": web, "save_artifact": save},
    )
    # save_artifact conflicts with prior web_search? No — different
    # path semantics. Actually save_artifact (write) vs web_search
    # (read, no path) → save has path, web doesn't → conservative
    # conflict. So save goes to its own stage, and the second
    # web_search slots back into stage 0 (compatible with the first
    # web_search and nothing else there).
    assert len(stages) == 2
    indices = {c.index for c in stages[0]}
    assert indices == {0, 2}
    assert [c.index for c in stages[1]] == [1]


def test_empty_batch_yields_no_stages() -> None:
    assert plan_stages([], {}) == []


def test_single_call_yields_one_stage() -> None:
    """Sanity — sequential floor is one stage of one call."""
    spec = _spec("anything")
    tool_calls = [{"name": "anything", "args": {}, "id": "tc-0"}]
    stages = plan_stages(tool_calls, {"anything": spec})
    assert len(stages) == 1
    assert len(stages[0]) == 1


# ---------------------------------------------------------------------------
# Module-level invariants
# ---------------------------------------------------------------------------


def test_max_tool_workers_is_eight() -> None:
    """Mini-ADR L-6 pins the per-stage concurrency cap at 8 to match
    Hermes ``_MAX_TOOL_WORKERS``."""
    assert MAX_TOOL_WORKERS == 8


# ---------------------------------------------------------------------------
# Stream TE-4 — irreversible tools are forced serial
# ---------------------------------------------------------------------------


def test_irreversible_conflicts_with_read() -> None:
    """An irreversible tool conflicts even with a pure read (which would
    otherwise be the safest possible sibling)."""
    irrev = _spec("bash", side_effect="irreversible")
    reader = _spec("read", read_only=True, path_args=("p",))
    a = _call(0, "bash", spec=irrev)
    b = _call(1, "read", args={"p": "x"}, spec=reader)
    assert conflicts(a, b)
    assert conflicts(b, a)


def test_irreversible_conflicts_with_disjoint_path_write() -> None:
    """Path-disjoint writes normally run in parallel, but an irreversible
    tool overrides that and still conflicts."""
    irrev = _spec("bash", side_effect="irreversible", path_args=("p",))
    writer = _spec("write", path_args=("p",))
    a = _call(0, "bash", args={"p": "a.txt"}, spec=irrev)
    b = _call(1, "write", args={"p": "b.txt"}, spec=writer)  # disjoint path
    assert conflicts(a, b)


def test_irreversible_overrides_parallel_safe() -> None:
    """A parallel-safe sibling cannot share a stage with an irreversible
    call — the irreversible check runs before the parallel-safe rule."""
    irrev = _spec("bash", side_effect="irreversible")
    par = ToolSpec(name="sub", description="d", is_parallel_safe=True)
    a = _call(0, "bash", spec=irrev)
    b = _call(1, "sub", spec=par)
    assert conflicts(a, b)


def test_irreversible_lands_alone_across_stages() -> None:
    """End-to-end: two reads + one irreversible bash → reads share a
    stage, bash runs solo in its own."""
    reader = _spec("read", read_only=True)
    irrev = _spec("bash", side_effect="irreversible")
    tool_calls = [
        {"name": "read", "args": {}, "id": "tc-0"},
        {"name": "bash", "args": {}, "id": "tc-1"},
        {"name": "read", "args": {}, "id": "tc-2"},
    ]
    stages = plan_stages(tool_calls, {"read": reader, "bash": irrev})
    # bash never shares a stage with anything.
    for stage in stages:
        names = {c.name for c in stage}
        if "bash" in names:
            assert names == {"bash"}
            assert len(stage) == 1
