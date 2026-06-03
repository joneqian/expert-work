"""Pydantic v2 schema tests for :class:`AgentSpec`."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from helix_agent.protocol import (
    AgentSpec,
    BuiltinToolSpec,
    HTTPToolSpec,
    MCPToolSpec,
    ModelSpec,
    SubAgentSpec,
    parse_agent_ref,
)

_MINIMAL: dict[str, Any] = {
    "apiVersion": "helix.io/v1",
    "kind": "Agent",
    "metadata": {
        "name": "code-reviewer",
        "version": "1.0.0",
        "tenant": "platform-eng",
    },
    "spec": {
        "tenant_config": {},
        "model": {"provider": "anthropic", "name": "claude-sonnet-4-5"},
        "system_prompt": {"template": "you are a reviewer"},
        "sandbox": {
            "resources": {"cpu": "1.0", "memory": "1Gi"},
            "network": {"egress": "proxy", "allowlist": ["api.anthropic.com"]},
            "filesystem": {"readonly_root": True, "writable": ["/workspace"]},
        },
    },
}


def _doc() -> dict[str, Any]:
    return deepcopy(_MINIMAL)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_manifest_validates() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.api_version == "helix.io/v1"
    assert spec.metadata.name == "code-reviewer"
    assert spec.spec.model.provider == "anthropic"
    # Defaults should have populated.
    assert spec.spec.workflow.type == "react"
    assert spec.spec.workflow.max_iterations == 12
    assert spec.spec.dynamic_context.inject_memory is True


def test_alias_apiversion_round_trips() -> None:
    spec = AgentSpec.model_validate(_doc())
    dumped = spec.model_dump(by_alias=True)
    assert dumped["apiVersion"] == "helix.io/v1"
    assert "api_version" not in dumped


def test_fallback_chain_accepted_when_acyclic() -> None:
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {"provider": "openai", "name": "gpt-4o"},
        {"provider": "anthropic", "name": "claude-haiku-4-5"},
    ]
    spec = AgentSpec.model_validate(doc)
    assert len(spec.spec.model.fallback) == 2


# ---------------------------------------------------------------------------
# Required-field validation
# ---------------------------------------------------------------------------


def test_missing_model_block_rejected() -> None:
    doc = _doc()
    del doc["spec"]["model"]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert any("model" in str(err["loc"]) for err in exc_info.value.errors())


def test_invalid_kind_rejected() -> None:
    doc = _doc()
    doc["kind"] = "Workflow"
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_unknown_extra_field_rejected() -> None:
    doc = _doc()
    doc["spec"]["mystery_field"] = "boom"
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert any("mystery_field" in str(err["loc"]) for err in exc_info.value.errors())


def test_temperature_out_of_range_rejected() -> None:
    doc = _doc()
    doc["spec"]["model"]["temperature"] = 3.5
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_negative_audit_retention_rejected() -> None:
    doc = _doc()
    doc["spec"]["tenant_config"]["audit_retention_days"] = 0
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# Lint rule #7 — network allowlist != ["*"]
# ---------------------------------------------------------------------------


def test_wildcard_allowlist_rejected() -> None:
    doc = _doc()
    doc["spec"]["sandbox"]["network"]["allowlist"] = ["*"]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "allowlist" in str(exc_info.value)


def test_explicit_wildcard_in_list_allowed() -> None:
    """A literal ``["*.internal", "*"]`` is fine — only the single-element
    ``["*"]`` is the dangerous wildcard. The list-of-one form is the only
    one we lint."""
    doc = _doc()
    doc["spec"]["sandbox"]["network"]["allowlist"] = ["*.internal", "*"]
    spec = AgentSpec.model_validate(doc)
    assert "*" in spec.spec.sandbox.network.allowlist


# ---------------------------------------------------------------------------
# Lint rule #8 — fallback chain must be acyclic
# ---------------------------------------------------------------------------


def test_self_referential_fallback_rejected() -> None:
    """Cycle: primary model recurses to itself in fallback."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {
            "provider": "anthropic",
            "name": "claude-sonnet-4-5",  # same as primary → cycle
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "cycle" in str(exc_info.value).lower()


def test_deep_cycle_rejected() -> None:
    """Cycle: A → B → A through nested fallback."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {
            "provider": "openai",
            "name": "gpt-4o",
            "fallback": [
                {"provider": "anthropic", "name": "claude-sonnet-4-5"},  # back to root
            ],
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        AgentSpec.model_validate(doc)
    assert "cycle" in str(exc_info.value).lower()


def test_two_providers_one_name_share_no_cycle() -> None:
    """Identity is (provider, name); same name on different provider is fine."""
    doc = _doc()
    doc["spec"]["model"]["fallback"] = [
        {"provider": "openai", "name": "claude-sonnet-4-5"},
    ]
    spec = AgentSpec.model_validate(doc)
    assert spec.spec.model.fallback[0].provider == "openai"


def test_model_spec_validate_directly() -> None:
    """ModelSpec works in isolation (used by the orchestrator router)."""
    model = ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o"})
    assert model.temperature == 0.2
    assert model.max_tokens == 4096


@pytest.mark.parametrize(
    "provider",
    ["kimi", "glm", "deepseek", "qwen", "doubao"],
)
def test_openai_compatible_providers_accepted(provider: str) -> None:
    """E.11.5 — five domestic OpenAI-compatible vendors must validate."""
    model = ModelSpec.model_validate({"provider": provider, "name": "test-model"})
    assert model.provider == provider


def test_unknown_provider_rejected() -> None:
    """Schema regression guard: unknown providers must still fail validation
    even after the E.11.5 Literal extension."""
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "bedrock", "name": "claude"})


def test_rate_limit_rpm_default_is_60() -> None:
    """E.12 — ``ModelSpec.rate_limit_rpm`` defaults to a conservative
    60 rpm matching the OpenAI free-tier ballpark."""
    model = ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o"})
    assert model.rate_limit_rpm == 60


def test_rate_limit_rpm_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o", "rate_limit_rpm": 0})
    with pytest.raises(ValidationError):
        ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o", "rate_limit_rpm": -1})


def test_rate_limit_rpm_custom_value_accepted() -> None:
    model = ModelSpec.model_validate(
        {"provider": "kimi", "name": "moonshot-v1-128k", "rate_limit_rpm": 3}
    )
    assert model.rate_limit_rpm == 3


# ---------------------------------------------------------------------------
# policies.approval — J.8 (Mini-ADR J-24)
# ---------------------------------------------------------------------------


def test_approval_fields_default_empty_and_24h() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.spec.policies.approval_required_tools == []
    assert spec.spec.policies.approval_timeout_s == 86400


def test_approval_required_tools_accepted() -> None:
    doc = _doc()
    doc["spec"]["policies"] = {
        "approval_required_tools": ["send_email", "http"],
        "approval_timeout_s": 3600,
    }
    spec = AgentSpec.model_validate(doc)
    assert spec.spec.policies.approval_required_tools == ["send_email", "http"]
    assert spec.spec.policies.approval_timeout_s == 3600


def test_approval_timeout_below_floor_rejected() -> None:
    """``approval_timeout_s`` floor is 60s — a too-short window is a config error."""
    doc = _doc()
    doc["spec"]["policies"] = {"approval_timeout_s": 30}
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_approval_timeout_above_ceiling_rejected() -> None:
    """Ceiling is 7 days — anything longer is almost certainly a mistake."""
    doc = _doc()
    doc["spec"]["policies"] = {"approval_timeout_s": 604801}
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# tools: discriminated union (Mini-ADR E-14)
# ---------------------------------------------------------------------------


def test_tools_default_is_empty_list() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.spec.tools == []


def test_tools_builtin_http_mcp_entries_validate() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [
        {"type": "builtin", "name": "web_search", "config": {"max_results": 5}},
        {"type": "http"},
        {"type": "mcp", "allow_tools": ["read_pr"]},
    ]
    spec = AgentSpec.model_validate(doc)
    builtin, http, mcp = spec.spec.tools
    assert isinstance(builtin, BuiltinToolSpec)
    assert isinstance(http, HTTPToolSpec)
    assert isinstance(mcp, MCPToolSpec)
    assert builtin.name == "web_search"
    assert builtin.config == {"max_results": 5}
    assert mcp.allow_tools == ["read_pr"]


def test_tools_mcp_allow_tools_defaults_empty() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"type": "mcp"}]
    spec = AgentSpec.model_validate(doc)
    entry = spec.spec.tools[0]
    assert isinstance(entry, MCPToolSpec)
    assert entry.allow_tools == []


def test_tools_unknown_type_rejected() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"type": "python", "name": "x"}]
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_tools_builtin_missing_name_rejected() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"type": "builtin"}]
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_tools_entry_extra_field_rejected() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"type": "http", "url": "https://x"}]
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_tools_entry_missing_discriminator_rejected() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"name": "web_search"}]
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# azure / self-hosted model fields (F-4)
# ---------------------------------------------------------------------------


def test_model_fields_default_to_none() -> None:
    model = ModelSpec.model_validate({"provider": "openai", "name": "gpt-4o"})
    assert model.base_url is None
    assert model.azure_deployment is None
    assert model.azure_api_version is None


def test_self_hosted_model_accepts_base_url() -> None:
    model = ModelSpec.model_validate(
        {
            "provider": "self-hosted",
            "name": "llama-3.1-70b",
            "base_url": "http://vllm.internal:8000",
        }
    )
    assert model.provider == "self-hosted"
    assert model.base_url == "http://vllm.internal:8000"


def test_azure_model_accepts_deployment_fields() -> None:
    model = ModelSpec.model_validate(
        {
            "provider": "azure",
            "name": "gpt-4o",
            "base_url": "https://res.openai.azure.com",
            "azure_deployment": "gpt-4o-deploy",
            "azure_api_version": "2024-10-21",
        }
    )
    assert model.azure_deployment == "gpt-4o-deploy"
    assert model.azure_api_version == "2024-10-21"


# ---------------------------------------------------------------------------
# subagents — agent-as-tool delegation (Stream J.4 / Mini-ADR J-12)
# ---------------------------------------------------------------------------


def test_parse_agent_ref_splits_name_and_version() -> None:
    assert parse_agent_ref("code-reviewer@1.0.0") == ("code-reviewer", "1.0.0")


@pytest.mark.parametrize(
    "bad_ref",
    ["noversion", "name@", "@version", "a@b@c", ""],
)
def test_parse_agent_ref_rejects_malformed(bad_ref: str) -> None:
    with pytest.raises(ValueError, match="agent_ref must be"):
        parse_agent_ref(bad_ref)


def test_subagent_spec_validates() -> None:
    sub = SubAgentSpec.model_validate(
        {
            "name": "research_helper",
            "agent_ref": "researcher@2.1.0",
            "description": "delegates deep research subtasks",
        }
    )
    assert sub.name == "research_helper"
    assert parse_agent_ref(sub.agent_ref) == ("researcher", "2.1.0")


@pytest.mark.parametrize(
    "bad_name",
    ["ResearchHelper", "research-helper", "1research", "_helper", "research helper"],
)
def test_subagent_name_must_be_snake_case(bad_name: str) -> None:
    with pytest.raises(ValidationError, match="snake_case"):
        SubAgentSpec.model_validate(
            {"name": bad_name, "agent_ref": "researcher@1.0.0", "description": "x"}
        )


def test_subagent_rejects_malformed_agent_ref() -> None:
    with pytest.raises(ValidationError, match="agent_ref must be"):
        SubAgentSpec.model_validate(
            {"name": "helper", "agent_ref": "noversion", "description": "x"}
        )


def test_subagent_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        SubAgentSpec.model_validate(
            {
                "name": "helper",
                "agent_ref": "researcher@1.0.0",
                "description": "x",
                "token_budget_fraction": 0.3,
            }
        )


def test_subagents_default_is_empty_list() -> None:
    spec = AgentSpec.model_validate(_doc())
    assert spec.spec.subagents == []


def test_subagents_block_validates_in_manifest() -> None:
    doc = _doc()
    doc["spec"]["subagents"] = [
        {"name": "researcher", "agent_ref": "deep-researcher@1.0.0", "description": "research"},
        {"name": "writer", "agent_ref": "doc-writer@2.0.0", "description": "drafting"},
    ]
    spec = AgentSpec.model_validate(doc)
    assert [s.name for s in spec.spec.subagents] == ["researcher", "writer"]


def test_subagent_self_delegation_rejected() -> None:
    """A subagent whose agent_ref points back at this agent is refused."""
    doc = _doc()  # metadata.name == "code-reviewer"
    doc["spec"]["subagents"] = [
        {"name": "myself", "agent_ref": "code-reviewer@2.0.0", "description": "loop"},
    ]
    with pytest.raises(ValidationError, match="self-delegation"):
        AgentSpec.model_validate(doc)


def test_duplicate_subagent_tool_name_rejected() -> None:
    doc = _doc()
    doc["spec"]["subagents"] = [
        {"name": "helper", "agent_ref": "agent-a@1.0.0", "description": "a"},
        {"name": "helper", "agent_ref": "agent-b@1.0.0", "description": "b"},
    ]
    with pytest.raises(ValidationError, match="duplicate subagent"):
        AgentSpec.model_validate(doc)


def test_subagent_name_colliding_with_builtin_tool_rejected() -> None:
    doc = _doc()
    doc["spec"]["tools"] = [{"type": "builtin", "name": "web_search"}]
    doc["spec"]["subagents"] = [
        {"name": "web_search", "agent_ref": "searcher@1.0.0", "description": "x"},
    ]
    with pytest.raises(ValidationError, match="collides with a declared builtin"):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# knowledge block — RAG (Stream J.5)
# ---------------------------------------------------------------------------


def test_knowledge_default_is_none() -> None:
    assert AgentSpec.model_validate(_doc()).spec.knowledge is None


def test_knowledge_block_validates_in_manifest() -> None:
    doc = _doc()
    doc["spec"]["knowledge"] = {"knowledge_base_refs": ["hr-policies", "eng-docs"]}
    spec = AgentSpec.model_validate(doc)
    assert spec.spec.knowledge is not None
    assert spec.spec.knowledge.knowledge_base_refs == ["hr-policies", "eng-docs"]


def test_knowledge_empty_refs_rejected() -> None:
    doc = _doc()
    doc["spec"]["knowledge"] = {"knowledge_base_refs": []}
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_knowledge_duplicate_refs_rejected() -> None:
    doc = _doc()
    doc["spec"]["knowledge"] = {"knowledge_base_refs": ["kb", "kb"]}
    with pytest.raises(ValidationError, match="duplicate knowledge base ref"):
        AgentSpec.model_validate(doc)


def test_knowledge_blank_ref_rejected() -> None:
    doc = _doc()
    doc["spec"]["knowledge"] = {"knowledge_base_refs": ["   "]}
    with pytest.raises(ValidationError, match="must be non-empty"):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# multimodal input — vision (Stream J.6)
# ---------------------------------------------------------------------------


def test_supports_vision_defaults_to_false() -> None:
    assert AgentSpec.model_validate(_doc()).spec.model.supports_vision is False


def test_supports_vision_accepts_explicit_true() -> None:
    doc = _doc()
    doc["spec"]["model"]["supports_vision"] = True
    assert AgentSpec.model_validate(doc).spec.model.supports_vision is True


def test_vision_block_default_is_none() -> None:
    assert AgentSpec.model_validate(_doc()).spec.vision is None


def test_vision_block_validates_in_manifest() -> None:
    doc = _doc()
    doc["spec"]["vision"] = {"model": {"provider": "qwen", "name": "qwen-vl-max"}}
    spec = AgentSpec.model_validate(doc)
    assert spec.spec.vision is not None
    assert spec.spec.vision.model.provider == "qwen"
    assert spec.spec.vision.model.name == "qwen-vl-max"


def test_vision_block_missing_model_rejected() -> None:
    doc = _doc()
    doc["spec"]["vision"] = {}
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


def test_vision_block_extra_field_rejected() -> None:
    doc = _doc()
    doc["spec"]["vision"] = {
        "model": {"provider": "qwen", "name": "qwen-vl-max"},
        "unknown": "x",
    }
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(doc)


# ---------------------------------------------------------------------------
# MCPToolSpec.servers — per-agent server selection (Stream V-E)
# ---------------------------------------------------------------------------


def test_mcp_tool_spec_defaults_empty_servers() -> None:
    spec = MCPToolSpec()
    assert spec.servers == []  # empty = all available servers
    assert spec.allow_tools == []


def test_mcp_tool_spec_accepts_servers() -> None:
    spec = MCPToolSpec(servers=["github", "linear"], allow_tools=["create_issue"])
    assert spec.servers == ["github", "linear"]


def test_mcp_tool_spec_backward_compatible_without_servers() -> None:
    # A manifest dict from before V-E (no "servers" key) must still parse,
    # defaulting servers to [].
    spec = MCPToolSpec.model_validate({"type": "mcp", "allow_tools": ["x"]})
    assert spec.servers == []


def test_mcp_tool_spec_still_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        MCPToolSpec.model_validate({"type": "mcp", "bogus": 1})
