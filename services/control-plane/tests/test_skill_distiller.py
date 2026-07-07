"""Tests for the SE-5a skill distiller (contrastive induction + abstraction guard).

Pure orchestration over rendered trajectory text via an injected model seam;
CI uses a fake model (real aux LLM is wired by the SE-6 worker).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from control_plane.skill_distiller import (
    DistillerReply,
    SkillDistiller,
    render_trajectory,
    tools_used,
)
from expert_work.protocol import StructuredOutputSpec
from expert_work.runtime.middleware import LLMOutputValidationError

_TENANT = UUID("33333333-3333-3333-3333-333333333333")


class FakeModel:
    def __init__(self, reply: str, parsed: dict[str, Any] | None = None) -> None:
        self.reply = reply
        self.parsed = parsed
        self.prompts: list[str] = []
        self.schemas: list[StructuredOutputSpec | None] = []

    async def __call__(
        self,
        *,
        prompt: str,
        tenant_id: UUID,
        model: str | None = None,
        output_schema: StructuredOutputSpec | None = None,
    ) -> DistillerReply:
        self.prompts.append(prompt)
        self.schemas.append(output_schema)
        return DistillerReply(text=self.reply, parsed=self.parsed)


def _draft_json(**over: Any) -> str:
    base: dict[str, Any] = {
        "name": "summarise-csv",
        "prompt_fragment": "When summarising a CSV, read headers first, then aggregate per column.",
        "tool_names": ["exec_python"],
        "description": "Summarise tabular data",
        "category": "data",
    }
    base.update(over)
    return json.dumps(base)


async def test_distill_returns_draft_from_success() -> None:
    model = FakeModel(_draft_json())
    distiller = SkillDistiller(model=model)
    draft = await distiller.distill(tenant_id=_TENANT, successes=["user: do it\nassistant: done"])
    assert draft is not None
    assert draft.name == "summarise-csv"
    assert "headers first" in draft.prompt_fragment
    assert draft.tool_names == ("exec_python",)
    assert draft.high_risk is True  # exec_python is high-risk


async def test_no_successes_returns_none() -> None:
    distiller = SkillDistiller(model=FakeModel(_draft_json()))
    assert await distiller.distill(tenant_id=_TENANT, successes=[]) is None


async def test_unparseable_reply_returns_none() -> None:
    distiller = SkillDistiller(model=FakeModel("sorry, no JSON here"))
    assert await distiller.distill(tenant_id=_TENANT, successes=["ok"]) is None


async def test_abstraction_guard_rejects_uuid_in_fragment() -> None:
    frag = "Fetch record 11111111-2222-3333-4444-555555555555 then summarise."
    distiller = SkillDistiller(model=FakeModel(_draft_json(prompt_fragment=frag)))
    assert await distiller.distill(tenant_id=_TENANT, successes=["ok"]) is None


async def test_abstraction_guard_rejects_long_digit_run() -> None:
    frag = "Call the endpoint with account 1234567890123 and proceed."
    distiller = SkillDistiller(model=FakeModel(_draft_json(prompt_fragment=frag)))
    assert await distiller.distill(tenant_id=_TENANT, successes=["ok"]) is None


async def test_tool_names_filtered_to_allowed() -> None:
    reply = _draft_json(tool_names=["exec_python", "made_up_tool"])
    distiller = SkillDistiller(model=FakeModel(reply))
    draft = await distiller.distill(
        tenant_id=_TENANT, successes=["ok"], allowed_tools=frozenset({"exec_python"})
    )
    assert draft is not None
    assert draft.tool_names == ("exec_python",)


async def test_benign_tools_are_not_high_risk() -> None:
    reply = _draft_json(tool_names=["knowledge_search"])
    distiller = SkillDistiller(model=FakeModel(reply))
    draft = await distiller.distill(tenant_id=_TENANT, successes=["ok"])
    assert draft is not None
    assert draft.high_risk is False


async def test_contrastive_prompt_includes_failures() -> None:
    model = FakeModel(_draft_json())
    distiller = SkillDistiller(model=model)
    await distiller.distill(
        tenant_id=_TENANT,
        successes=["assistant: correct approach"],
        failures=["assistant: wrong approach"],
    )
    prompt = model.prompts[0]
    assert "wrong approach" in prompt
    assert "correct approach" in prompt


async def test_empty_fragment_rejected() -> None:
    distiller = SkillDistiller(model=FakeModel(_draft_json(prompt_fragment="  ")))
    assert await distiller.distill(tenant_id=_TENANT, successes=["ok"]) is None


# --------------------------------------------------------------------------- #
# RT-1 PR-2: structured output through the model seam
# --------------------------------------------------------------------------- #


async def test_distill_passes_output_schema() -> None:
    model = FakeModel(_draft_json())
    await SkillDistiller(model=model).distill(tenant_id=_TENANT, successes=["ok"])
    assert len(model.schemas) == 1
    spec = model.schemas[0]
    assert spec is not None
    assert spec.name == "distilled_skill_draft"
    # The draft's substance is required; the descriptive fields keep
    # their pre-RT-1 ``.get(...)`` optionality (model defaults).
    assert set(spec.schema["required"]) == {"name", "prompt_fragment"}
    assert set(spec.schema["properties"]) == {
        "name",
        "prompt_fragment",
        "tool_names",
        "description",
        "category",
    }


async def test_distill_tolerates_missing_descriptive_fields() -> None:
    """Pre-RT-1 ``.get(...)`` pin — a reply with only the substance
    fields still yields a usable draft with defaulted extras."""
    reply = json.dumps(
        {"name": "summarise-csv", "prompt_fragment": "Read headers first, then aggregate."}
    )
    draft = await SkillDistiller(model=FakeModel(reply)).distill(
        tenant_id=_TENANT, successes=["ok"]
    )
    assert draft is not None
    assert draft.tool_names == ()
    assert draft.description == ""
    assert draft.category is None


async def test_distill_prefers_router_parsed_dict() -> None:
    """The validated ``parsed`` dict wins over raw text."""
    model = FakeModel(
        "prose that is not JSON",
        parsed={
            "name": "summarise-csv",
            "prompt_fragment": "Read headers first, then aggregate per column.",
            "tool_names": ["knowledge_search"],
            "description": "Summarise tabular data",
            "category": None,
        },
    )
    draft = await SkillDistiller(model=model).distill(tenant_id=_TENANT, successes=["ok"])
    assert draft is not None
    assert draft.name == "summarise-csv"
    assert draft.category is None


async def test_distill_validation_exhausted_returns_none() -> None:
    """RT-ADR-3 — LLMOutputValidationError == unusable reply → None
    (distillation failed), never a raise up to the SE-6 worker."""

    class _ExplodingModel:
        async def __call__(
            self,
            *,
            prompt: str,
            tenant_id: UUID,
            model: str | None = None,
            output_schema: StructuredOutputSpec | None = None,
        ) -> DistillerReply:
            raise LLMOutputValidationError("still invalid after retries")

    draft = await SkillDistiller(model=_ExplodingModel()).distill(
        tenant_id=_TENANT, successes=["ok"]
    )
    assert draft is None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_tools_used_extracts_tool_call_names() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "exec_python", "args": {}}]},
        {"role": "tool", "content": "42", "tool_call_id": "x"},
        {"role": "assistant", "content": "done"},
    ]
    assert tools_used(messages) == frozenset({"exec_python"})


def test_render_trajectory_includes_roles_and_content() -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "summarise"},
        {"role": "assistant", "content": "sure"},
    ]
    text = render_trajectory(messages)
    assert "user" in text
    assert "summarise" in text
    assert "assistant" in text
