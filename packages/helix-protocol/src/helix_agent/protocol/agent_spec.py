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

import re
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
    #: Whether this model natively accepts image content blocks (J.6).
    #: Manifest-author-declared — the platform does NOT infer it from the
    #: model name. ``true`` selects J.6 Path A (images flow into the
    #: ``HumanMessage`` and the model sees pixels directly); ``false``
    #: (default) selects Path B (the ``ask_image`` tool routes to a
    #: separate VL model declared in ``spec.vision``).
    supports_vision: bool = False


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
    #: Stream J.15 — when ``True``, ``exec_python`` mounts the run's
    #: user's persistent workspace volume at ``/workspace`` so files
    #: survive across runs. ``False`` (default) → an ephemeral tmpfs.
    persistent_workspace: bool = False


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


class LongTermMemorySpec(BaseModel):
    """Manifest ``memory.long_term`` block — Stream J.3.

    Presence activates cross-session memory: a ``memory_recall`` node
    injects relevant past memories at run start, and a
    ``memory_writeback`` node extracts new ones at run end.
    """

    model_config = ConfigDict(extra="forbid")

    retrieve_top_k: int = Field(default=5, gt=0, description="memories injected per run")
    write_back: bool = Field(default=True, description="extract + persist memories at run end")


class MemorySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_term: dict[str, Any] | None = None
    long_term: LongTermMemorySpec | None = None


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


class KnowledgeSpec(BaseModel):
    """Manifest ``knowledge:`` block — Stream J.5 RAG.

    Presence activates the ``knowledge_search`` tool;
    ``knowledge_base_refs`` names the tenant knowledge bases the agent
    may query. The names are resolved to base ids at search time, so a
    base may be created after the agent is deployed.
    """

    model_config = ConfigDict(extra="forbid")

    knowledge_base_refs: list[str] = Field(
        min_length=1,
        description="names of tenant knowledge bases this agent may search",
    )

    @model_validator(mode="after")
    def _check_refs(self) -> KnowledgeSpec:
        seen: set[str] = set()
        for ref in self.knowledge_base_refs:
            if not ref.strip():
                msg = "knowledge_base_refs entries must be non-empty"
                raise ValueError(msg)
            if ref in seen:
                msg = f"duplicate knowledge base ref {ref!r}"
                raise ValueError(msg)
            seen.add(ref)
        return self


class VisionSpec(BaseModel):
    """Manifest ``vision:`` block — Stream J.6 multimodal input (Path B).

    Declared only when the agent's main ``model`` is NOT vision-capable
    (``model.supports_vision`` is ``false``). Presence activates the
    ``ask_image`` tool, which routes image-understanding questions to
    ``model`` — a separate VL model — leaving the main reasoning loop on
    the strong text model. A ``vision:`` block on a vision-capable agent
    is rejected at agent-build time: the two J.6 paths are mutually
    exclusive (see ``docs/streams/STREAM-J-DESIGN.md`` § 13).
    """

    model_config = ConfigDict(extra="forbid")

    model: ModelSpec = Field(description="VL model the ask_image tool routes to")


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["react", "plan_execute", "custom"] = "react"
    max_iterations: int = Field(default=12, gt=0)
    early_stop: dict[str, Any] = Field(default_factory=dict)
    builder: str | None = None


class CacheSpec(BaseModel):
    """Stream K.K4 (Mini-ADR K-3) — per-agent LLM response cache opt-out.

    The orchestrator's response cache (E.13) wraps every cacheable LLM
    call by default. Agents whose prompts contain time-sensitive content
    (date / latest-news lookups / per-call randomness the LLM has to
    re-compute) must disable it — otherwise a cache hit returns a stale
    answer, sacrificing correctness for latency.

    The opt-out lives on the manifest, not on ``RunRequest``: an agent
    declares its caching behaviour once, applies to every run. Per-call
    skip would let the same agent behave differently across requests
    (Mini-ADR K-3) and undermine cache key semantics.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description=(
            "When false the orchestrator does not attach the LLM cache "
            "lookup / store middlewares for this agent — every LLM call "
            "goes through to the provider, results are not stored."
        ),
    )


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
#: is M1-F — declaring it fails manifest validation here (no matching
#: ``type`` variant). Sub-agents are declared in ``spec.subagents``
#: (Stream J.4), not as a tool entry.
ToolSpecEntry = Annotated[
    BuiltinToolSpec | HTTPToolSpec | MCPToolSpec,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# subagents — agent-as-tool delegation (STREAM-J-DESIGN J.4 / Mini-ADR J-12)
# ---------------------------------------------------------------------------


#: Snake_case identifier — same shape the parent LLM expects for a tool name.
_SUBAGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_agent_ref(ref: str) -> tuple[str, str]:
    """Split a ``name@version`` agent reference into ``(name, version)``.

    Raises :class:`ValueError` when ``ref`` is not exactly one ``name``
    and one ``version`` separated by a single ``@`` — used both by
    :class:`SubAgentSpec` validation and the orchestrator's
    ``SubAgentTool`` when resolving the referenced AgentSpec.
    """
    parts = ref.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        msg = f"agent_ref must be 'name@version', got {ref!r}."
        raise ValueError(msg)
    return parts[0], parts[1]


class SubAgentSpec(BaseModel):
    """One entry in ``spec.subagents`` — Stream J.4.

    Declares a deployed AgentSpec the parent agent may delegate to. The
    orchestrator wraps each entry into a named ``SubAgentTool`` so the
    parent's LLM sees delegation as an ordinary tool call (a named tool
    gives the LLM a clearer selection signal than a single generic
    ``task`` tool — Mini-ADR J-12).
    """

    model_config = ConfigDict(extra="forbid")

    #: Tool name handed to the parent LLM — snake_case; the identifier
    #: the parent calls to delegate a subtask.
    name: str = Field(min_length=1, max_length=64)
    #: ``name@version`` reference to a deployed AgentSpec in the same tenant.
    agent_ref: str = Field(min_length=3)
    #: Tool description shown to the parent LLM for delegation decisions.
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_fields(self) -> SubAgentSpec:
        if not _SUBAGENT_NAME_RE.match(self.name):
            msg = f"subagent name must be snake_case ([a-z][a-z0-9_]*), got {self.name!r}."
            raise ValueError(msg)
        parse_agent_ref(self.agent_ref)  # raises ValueError on bad format
        return self


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
    subagents: list[SubAgentSpec] = Field(
        default_factory=list,
        description="Stream J.4 — deployed agents this agent may delegate to (agent-as-tool)",
    )
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
    knowledge: KnowledgeSpec | None = Field(
        default=None,
        description="Stream J.5 — knowledge bases (RAG) this agent may search",
    )
    vision: VisionSpec | None = Field(
        default=None,
        description="Stream J.6 — VL model for the ask_image tool (Path B); "
        "declared only when model.supports_vision is false",
    )
    workflow: WorkflowSpec = Field(default_factory=WorkflowSpec)
    cache: CacheSpec = Field(
        default_factory=CacheSpec,
        description=(
            "Stream K.K4 — LLM response cache toggle. Default enabled; "
            "agents with time-sensitive prompts must set ``enabled: false``."
        ),
    )
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

    @model_validator(mode="after")
    def _check_subagents(self) -> AgentSpec:
        """J.4 — validate the ``spec.subagents`` block.

        Rejects three manifest-local errors: (1) self-delegation — a
        subagent whose ``agent_ref`` points back at this agent;
        (2) two subagents sharing a tool name; (3) a subagent tool name
        colliding with a declared ``builtin`` tool. Cross-manifest cycles
        (A→B→A) are not caught here — the orchestrator bounds them
        structurally via the build-time depth limit (Mini-ADR J-12).
        """
        builtin_names = {t.name for t in self.spec.tools if isinstance(t, BuiltinToolSpec)}
        seen: set[str] = set()
        for sub in self.spec.subagents:
            if sub.name in seen:
                msg = f"duplicate subagent tool name {sub.name!r}."
                raise ValueError(msg)
            seen.add(sub.name)
            if sub.name in builtin_names:
                msg = f"subagent tool name {sub.name!r} collides with a declared builtin tool."
                raise ValueError(msg)
            ref_name, _ = parse_agent_ref(sub.agent_ref)
            if ref_name == self.metadata.name:
                msg = (
                    f"subagent {sub.name!r} references the agent itself "
                    f"({self.metadata.name!r}) — self-delegation is not allowed."
                )
                raise ValueError(msg)
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
