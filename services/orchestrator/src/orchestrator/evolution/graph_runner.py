"""Real agent-graph ``TaskRunner`` adapter (Stream SE, SE-4c).

SE-4b's :class:`~orchestrator.evolution.replay.ReplayRunner` orchestrates
replay against the ``TaskRunner`` seam. This module supplies the *real*
implementation: it builds two agents that differ only in whether they enable
the candidate skill, runs each task through the compiled LangGraph, and returns
the agent's final answer text.

* baseline = the base ``AgentSpec`` with the candidate skill removed.
* treatment = the base ``AgentSpec`` with the candidate skill pinned (``name@N``).

Both specs are built once and cached; ``run`` picks the variant per call.

High-risk skills need no special handling here: the agent the injected
``agent_builder`` produces already routes exec tools through the gVisor sandbox
(the control-plane builder wires the sandboxed ``ToolEnv``), so a high-risk
candidate is replayed sandboxed by construction (Mini-ADR SE-A6). The
build-vs-not-build of the variants is pure and unit-tested; the real
end-to-end with a live model is validated by the eval harness / SE-9 benchmark.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from expert_work.protocol import AgentSpec
from orchestrator.agent_factory import BuiltAgent

__all__ = [
    "AgentBuilder",
    "ConfigFactory",
    "GraphReplayTaskRunner",
    "with_candidate_skill",
    "without_candidate_skill",
]

#: Builds a runnable agent from a spec (bound to the tenant's deps by the caller).
AgentBuilder = Callable[[AgentSpec], Awaitable[BuiltAgent]]
#: Produces a per-run config (fresh ``thread_id`` etc.) for ``(case_id, with_skill)``.
ConfigFactory = Callable[[str, bool], RunnableConfig]


def _skill_ref_name(ref: str) -> str:
    return ref.split("@", 1)[0]


def _replace_skills(spec: AgentSpec, skills: list[str]) -> AgentSpec:
    return spec.model_copy(update={"spec": spec.spec.model_copy(update={"skills": skills})})


def without_candidate_skill(spec: AgentSpec, *, skill_name: str) -> AgentSpec:
    """Return a copy of ``spec`` with any reference to ``skill_name`` removed."""
    kept = [ref for ref in spec.spec.skills if _skill_ref_name(ref) != skill_name]
    return _replace_skills(spec, kept)


def with_candidate_skill(spec: AgentSpec, *, skill_name: str, skill_version: int) -> AgentSpec:
    """Return a copy of ``spec`` with ``skill_name`` pinned to ``skill_version``.

    Any existing reference to the same skill name is replaced (no duplicate),
    so the result stays valid against the manifest's skill-dedup rule.
    """
    kept = [ref for ref in spec.spec.skills if _skill_ref_name(ref) != skill_name]
    kept.append(f"{skill_name}@{skill_version}")
    return _replace_skills(spec, kept)


def _final_answer(messages: Sequence[BaseMessage]) -> str | None:
    """Return the last ``AIMessage``'s content as text, or ``None``.

    The agent graph ends on a no-tool-calls ``AIMessage`` whose content is the
    answer (same convention as the sub-agent tool).
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return None


@dataclass
class GraphReplayTaskRunner:
    """A :class:`~orchestrator.evolution.replay.TaskRunner` backed by real graphs."""

    baseline_spec: AgentSpec
    treatment_spec: AgentSpec
    agent_builder: AgentBuilder
    config_factory: ConfigFactory
    _cache: dict[bool, BuiltAgent] = field(default_factory=dict, init=False)

    @classmethod
    def from_candidate(
        cls,
        base_spec: AgentSpec,
        *,
        skill_name: str,
        skill_version: int,
        agent_builder: AgentBuilder,
        config_factory: ConfigFactory,
    ) -> GraphReplayTaskRunner:
        """Build a runner whose treatment enables ``skill_name@skill_version``."""
        return cls(
            baseline_spec=without_candidate_skill(base_spec, skill_name=skill_name),
            treatment_spec=with_candidate_skill(
                base_spec, skill_name=skill_name, skill_version=skill_version
            ),
            agent_builder=agent_builder,
            config_factory=config_factory,
        )

    async def _built(self, with_skill: bool) -> BuiltAgent:
        if with_skill not in self._cache:
            spec = self.treatment_spec if with_skill else self.baseline_spec
            self._cache[with_skill] = await self.agent_builder(spec)
        return self._cache[with_skill]

    async def run(self, *, case_id: str, prompt: str, with_skill: bool) -> str:
        built = await self._built(with_skill)
        config = self.config_factory(case_id, with_skill)
        result = await built.graph.ainvoke(
            {
                "messages": [
                    SystemMessage(content=built.system_prompt),
                    HumanMessage(content=prompt),
                ],
                "step_count": 0,
                "max_steps": built.max_steps,
            },
            config,
        )
        messages = list(result.get("messages", [])) if isinstance(result, dict) else []
        return _final_answer(messages) or ""
