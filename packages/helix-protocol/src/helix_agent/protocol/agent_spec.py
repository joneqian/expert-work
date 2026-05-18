"""``AgentSpec`` — the strongly-typed runtime contract behind ``AgentManifest``.

Stream B.4 ships the canonical Pydantic v2 schema; rendering YAML →
``AgentSpec`` lives in ``services/control-plane/src/control_plane/manifest``
so this package stays framework-free.

Coverage (per [STREAM-B-DESIGN ADR B-3](../../../../docs/streams/STREAM-B-DESIGN.md)):

* Full required-field / type validation for every block.
* Two lint rules baked into ``model_validator``\\s:
    * Network ``allowlist`` MUST NOT be a wildcard ``["*"]`` (rule #7).
    * Model ``fallback`` chain MUST be acyclic (rule #8).

Rules #1-6 (secret refs, MCP allowlist, sub-agent resolution, sandbox
quota, Python package, etc.) defer to the Streams that own their data
sources (C secrets, E tool dispatcher, F sandbox, C.5 quota).

Sub-types intentionally remain permissive (``dict[str, Any]``) for blocks
that downstream Streams will tighten — locking the schema today would
force a churn-heavy migration when each owning Stream lands.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from helix_agent.protocol.reflection import ReflectionSpec

# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------


class AgentMetadata(BaseModel):
    """``metadata`` block. ``tenant`` is the logical tenant slug,
    NOT the UUID — the loader maps it to ``tenant_id`` via session.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1)
    tenant: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# tenant_config
# ---------------------------------------------------------------------------


class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compliance_pack: Literal["hipaa", "gdpr", "sox"] | None = None
    pii_fields: list[str] = Field(default_factory=list)
    isolation_level: Literal["shared", "dedicated_sandbox", "dedicated_node"] = "shared"
    audit_retention_days: int = Field(default=90, ge=1)
    data_residency: str | None = None


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------


class ModelSpec(BaseModel):
    """LLM provider + fallback chain.

    Lint rule #8: the ``fallback`` chain must not contain a cycle —
    verified post-construction in :meth:`AgentSpec._check_fallback_chain`
    because cycle detection needs to walk the entire tree.
    """

    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "anthropic",
        "openai",
        "azure",
        "self-hosted",
        # OpenAI-compatible regional vendors (E.11.5) — all use the
        # OpenAI Chat Completions wire format; see
        # ``orchestrator.llm.providers.openai_compatible`` for the
        # base-URL + path mappings.
        "kimi",
        "glm",
        "deepseek",
        "qwen",
        "doubao",
    ]
    name: str = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    #: Requests per minute the runtime is allowed to send to this
    #: provider key (E.12). Consumed by
    #: ``orchestrator.llm.rate_limit.RateLimitedProvider`` which wraps
    #: the provider's ``complete()`` in a token bucket — over-limit
    #: calls **await** rather than bursting into 429s (which would
    #: poison the E.4 breaker and noise up the fallback chain).
    rate_limit_rpm: int = Field(default=60, gt=0)
    #: Reference to the provider API key — a ``secret://`` URI resolved
    #: at agent-build time via the SecretStore (ADR-0007 / F.6). The
    #: manifest never embeds the key value itself. ``None`` is valid in
    #: the schema (keeps existing manifests / tests loading) but the
    #: agent factory rejects it: a provider with no key cannot be built.
    api_key_ref: str | None = None
    #: Base URL for the ``self-hosted`` provider (an OpenAI-compatible
    #: server — vLLM / Ollama / …) and the ``azure`` resource endpoint
    #: (``https://<resource>.openai.azure.com``). Ignored by the
    #: built-in providers. The agent factory rejects a ``self-hosted`` /
    #: ``azure`` model without it.
    base_url: str | None = None
    #: Azure OpenAI deployment name — the path segment in the
    #: deployment-style chat-completions URL. Required for ``azure``.
    azure_deployment: str | None = None
    #: Azure OpenAI ``api-version`` query parameter. Required for
    #: ``azure``.
    azure_api_version: str | None = None
    fallback: list[ModelSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# system_prompt + dynamic_context
# ---------------------------------------------------------------------------


class SystemPromptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str = Field(min_length=1)


class CustomReminderSpec(BaseModel):
    """One entry in ``dynamic_context.custom_reminders``."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    template: str = Field(min_length=1)


class DynamicContextSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inject_memory: bool = True
    inject_current_date: bool = True
    custom_reminders: list[CustomReminderSpec] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------


class ResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu: str = Field(min_length=1)
    memory: str = Field(min_length=1)
    pids: int = Field(default=256, gt=0)
    timeout_s: int = Field(default=600, gt=0)


class NetworkSpec(BaseModel):
    """Sandbox egress policy. Lint rule #7: allowlist != ``["*"]``."""

    model_config = ConfigDict(extra="forbid")

    egress: Literal["none", "direct", "proxy"] = "proxy"
    allowlist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_wildcard(self) -> NetworkSpec:
        if self.allowlist == ["*"]:
            msg = (
                "sandbox.network.allowlist must not be ['*']; list explicit "
                "domains (subsystems/21-network-policy lint rule #7)."
            )
            raise ValueError(msg)
        return self


class MountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tmpfs", "bind", "volume"]
    target: str = Field(min_length=1)
    size: str | None = None
    source: str | None = None


class FilesystemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readonly_root: bool = True
    writable: list[str] = Field(default_factory=list)
    mounts: list[MountSpec] = Field(default_factory=list)


class SandboxSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: Literal["gvisor", "docker", "none"] = "gvisor"
    image: str | None = None
    image_build: dict[str, Any] | None = None
    resources: ResourceSpec
    network: NetworkSpec
    filesystem: FilesystemSpec


# ---------------------------------------------------------------------------
# memory / workflow / policies / code / observability
# ---------------------------------------------------------------------------


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_term: dict[str, Any] | None = None
    long_term: dict[str, Any] | None = None


class RouteRule(BaseModel):
    """One model-routing rule — Stream J.11.

    Picks a distinct :class:`ModelSpec` (with its own fallback chain)
    for a class of LLM step. ``when`` covers the step classes that have
    a runtime emitter today; later Streams extend it (e.g. ``vision``
    with J.6 multimodal input).
    """

    model_config = ConfigDict(extra="forbid")

    when: Literal["planning", "reflection"]
    model: ModelSpec


class RoutingSpec(BaseModel):
    """Manifest ``routing:`` block — per-step-class model selection (J.11).

    A step class with no matching rule falls back to the agent's
    top-level ``model``.
    """

    model_config = ConfigDict(extra="forbid")

    rules: list[RouteRule] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["react", "plan_execute", "custom"] = "react"
    max_iterations: int = Field(default=12, gt=0)
    early_stop: dict[str, Any] = Field(default_factory=dict)
    builder: str | None = None


class PolicySpec(BaseModel):
    """Tightening to per-field types is deferred to the owning Streams
    (C.5 quota, D.2 PII, E.6 fallback). Permissive dicts now, schemas
    later."""

    model_config = ConfigDict(extra="forbid")

    rate_limit: dict[str, Any] = Field(default_factory=dict)
    pii: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    context_compression: dict[str, Any] = Field(default_factory=dict)


class CodePackageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: str = Field(min_length=1)
    requirements: list[str] = Field(default_factory=list)


class ObservabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace: str = "opentelemetry"
    log_level: str = "info"
    redact_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# tools — manifest-side declarations (STREAM-E-DESIGN Mini-ADR E-14)
# ---------------------------------------------------------------------------


class BuiltinToolSpec(BaseModel):
    """A platform built-in tool. M0 ships ``web_search``; ``config``
    carries per-tool knobs (e.g. ``{engine, max_results}``)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["builtin"] = "builtin"
    name: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class HTTPToolSpec(BaseModel):
    """Enable the generic HTTP tool for this agent. The URL allowlist is
    tenant-scoped (``tenant_config.http_tool_allowlist``), not declared
    here — see Mini-ADR E-14."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http"] = "http"


class MCPToolSpec(BaseModel):
    """Enable MCP tools for this agent. MCP servers are tenant-scoped
    (``tenant_config.mcp_servers``). ``allow_tools`` optionally filters
    which advertised tools the agent sees — empty means all."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp"] = "mcp"
    allow_tools: list[str] = Field(default_factory=list)


#: Discriminated union of the M0-supported tool declarations. ``python``
#: / ``subagent`` are M1-F — declaring them fails manifest validation
#: here (no matching ``type`` variant).
ToolSpecEntry = Annotated[
    BuiltinToolSpec | HTTPToolSpec | MCPToolSpec,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# spec body + envelope
# ---------------------------------------------------------------------------


class AgentSpecBody(BaseModel):
    """The ``spec:`` block. ``tools`` is a ``type``-discriminated union
    (Mini-ADR E-14); the orchestrator's ``build_tool_registry`` maps
    each entry to a concrete tool adapter."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    extends: str | None = None
    tenant_config: TenantConfig
    model: ModelSpec
    system_prompt: SystemPromptSpec
    dynamic_context: DynamicContextSpec = Field(default_factory=DynamicContextSpec)
    tools: list[ToolSpecEntry] = Field(default_factory=list)
    sandbox: SandboxSpec
    memory: MemorySpec | None = None
    reflection: ReflectionSpec | None = Field(
        default=None,
        description="Stream J.2 — presence activates the self-critique reflect node",
    )
    routing: RoutingSpec | None = Field(
        default=None,
        description="Stream J.11 — per-step-class model selection (planner / reflect)",
    )
    workflow: WorkflowSpec = Field(default_factory=WorkflowSpec)
    policies: PolicySpec = Field(default_factory=PolicySpec)
    code: CodePackageSpec | None = None
    hooks: dict[str, str] = Field(default_factory=dict)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)


class AgentSpec(BaseModel):
    """Top-level Pydantic root. Aliased ``apiVersion`` because the YAML
    uses camelCase per Kubernetes-style convention."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: str = Field(alias="apiVersion", min_length=1)
    kind: Literal["Agent"]
    metadata: AgentMetadata
    spec: AgentSpecBody

    @model_validator(mode="after")
    def _check_fallback_chain(self) -> AgentSpec:
        """Lint rule #8 — model.fallback DAG must be acyclic.

        Identity is ``(provider, name)``. A cycle in the manifest means
        the engine could ping-pong between two providers under fallback,
        so we refuse to load it.
        """
        visited: set[tuple[str, str]] = set()
        stack: list[ModelSpec] = [self.spec.model]
        while stack:
            current = stack.pop()
            ident = (current.provider, current.name)
            if ident in visited:
                msg = (
                    f"model.fallback chain contains a cycle at "
                    f"provider={current.provider!r}, name={current.name!r}."
                )
                raise ValueError(msg)
            visited.add(ident)
            stack.extend(current.fallback)
        return self


ModelSpec.model_rebuild()
AgentSpecBody.model_rebuild()
AgentSpec.model_rebuild()


# ---------------------------------------------------------------------------
# Persisted-envelope record (Stream B.5 registry rows)
# ---------------------------------------------------------------------------


class AgentSpecStatus(StrEnum):
    """``agent_spec.status`` enum; ``deleted`` is the soft-delete state."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class AgentSpecRecord(BaseModel):
    """One row of the ``agent_spec`` registry table."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    name: str
    version: str
    spec: AgentSpec
    spec_sha256: str = Field(min_length=64, max_length=64)
    status: AgentSpecStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
