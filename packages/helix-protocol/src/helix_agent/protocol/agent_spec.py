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
from helix_agent.protocol.trigger import TriggerSpec

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
    #: manifest never embeds the key value itself. **Stream Y-2 deprecated
    #: for manifests**: agent builds ignore this field and resolve the key
    #: from the platform credential (LLM spend must be platform-metered);
    #: ``None`` is the normal case. The field is retained because the
    #: control-plane's internal rerank/embed/aux plumbing still pins an
    #: already-platform-resolved ``secret://`` ref here when calling
    #: ``build_llm_router`` directly (no metering bypass).
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
    #: Stream L.L1 — opt out of Anthropic prompt caching for this model.
    #: Defaults ``True`` because the Anthropic adapter only flips the
    #: ``cache_control`` markers on when the provider is ``anthropic`` AND
    #: this flag is set; non-Anthropic providers ignore it. Set ``False``
    #: on Anthropic models where the agent author wants to disable
    #: caching (debugging, eval reproducibility) — Mini-ADR L-1.
    cache_enabled: bool = True
    #: Stream L.L2 — declared upstream context window in tokens. The
    #: agent_node preflight compares the estimated prompt size against
    #: ``context_window * policies.context_compression.threshold_pct``
    #: and triggers :class:`ContextCompressor` when exceeded (Mini-ADR
    #: L-2). Default 200_000 matches Claude 3.5 / Sonnet 4 long-context;
    #: agents pinned to smaller-window models should override here.
    context_window: int = Field(default=200_000, gt=0)
    # Stream CM-9 (Mini-ADR CM-J2) — compute-control knobs. ``effort``
    # maps to Anthropic ``output_config.effort`` (None → omitted, API
    # default applies); ``adaptive_thinking`` sends
    # ``thinking: {"type": "adaptive"}`` (4.6+ — interleaved thinking is
    # implied, no beta header). Both default off so existing manifests
    # are byte-for-byte unchanged. The factory rejects ``effort`` on
    # models whose catalog entry lacks the capability (fail-fast at
    # build instead of a runtime 400).
    effort: Literal["low", "medium", "high", "max"] | None = Field(
        default=None, description="Anthropic output_config.effort level"
    )
    adaptive_thinking: bool = Field(
        default=False, description="send thinking: {type: adaptive} (Anthropic 4.6+)"
    )


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
    #: Stream OFFICE-1a — selects which prebuilt sandbox image variant the
    #: tools run in. ``None``/``"minimal"`` → the default pure-stdlib image;
    #: ``"office"`` → the image with office libraries (pandas/openpyxl/
    #: python-docx/python-pptx/pypdf/Pillow) + CJK fonts. A controlled enum
    #: (not the free-form ``image`` field) so a manifest can't point the
    #: sandbox at an arbitrary image. The supervisor maps it to a configured
    #: image name; an unknown value falls back to the default.
    image_variant: Literal["minimal", "office"] | None = None
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
    # Capability Uplift Sprint #8 — Mini-ADR U-8.
    # ``per_session`` (default): inject the recalled memory list at a
    # stable prefix slot once per session and mark it with a cache
    # anchor so the Anthropic prompt cache covers the prefix
    # ``[system, task, memories]`` across all turns. ``per_turn``:
    # legacy J.3 behavior — re-render the memory list at the tail of
    # each turn's messages. Kept as an escape hatch for agents that
    # self-modify their memory mid-session and need the next turn to
    # see the change (M0 has no such tool; reserved for M1).
    recall_mode: Literal["per_session", "per_turn"] = Field(
        default="per_session",
        description=(
            "where the recalled memory list is rendered in the prompt; "
            "``per_session`` enables Anthropic prompt-cache anchoring"
        ),
    )
    # Stream CM-7 (Mini-ADR CM-H3/H6) — Mem0-style extract→update at the
    # run-end write-back: extracted memories are reconciled against
    # similar existing ones (ADD / UPDATE / DELETE / NOOP) instead of
    # written blindly, so paraphrased duplicates stop piling up and
    # contradicted facts get superseded. ``False`` restores the
    # pre-CM-7 direct write byte-for-byte. The CM-3 pre-compaction
    # flush always writes directly (latency-sensitive, inside a turn).
    reconcile_writes: bool = Field(
        default=True,
        description="reconcile run-end extracted memories against similar existing ones",
    )


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

    Mini-ADR J-33 (J.6.补强-4) — ``fallbacks`` is the VL-side mirror of
    E.11 LLM Provider Fallback Chain. VL providers (Qwen-VL / GLM-4V)
    have lower stability than the strong text models; a single hard
    failure on the primary should fall over to a secondary VL model
    rather than 500 the whole image question. Reuse of
    :class:`LLMRouter` keeps the error-classification + fallback
    semantics identical to the main reasoning loop.
    """

    model_config = ConfigDict(extra="forbid")

    model: ModelSpec = Field(description="VL model the ask_image tool routes to")
    fallbacks: list[ModelSpec] = Field(
        default_factory=list,
        description="Mini-ADR J-33 — VL providers tried in order when the primary fails",
    )


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


class ContextCompressionPolicy(BaseModel):
    """Stream L.L2 — per-agent context compression knobs.

    Drives :class:`~orchestrator.context.compressor.ContextCompressor`:
    the agent_node preflight triggers compression when the estimated
    prompt size exceeds ``context_window * threshold_pct``. The
    compressor preserves the first ``head_keep`` and last ``tail_keep``
    non-system messages, summarising everything in between via an LLM
    call. ``max_passes`` caps repeated compression attempts before the
    run aborts with ``ContextOverflowError`` (Mini-ADR L-2 — no
    silent fallback).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    threshold_pct: float = Field(default=0.7, gt=0.0, le=1.0)
    head_keep: int = Field(default=4, ge=0)
    tail_keep: int = Field(default=6, ge=0)
    #: Stream CM-3 — flush the about-to-be-discarded middle to long-term
    #: memory before each compaction pass summarises it away. Only active
    #: when long-term memory write-back is enabled (the flush reuses the
    #: write-back extraction path); a no-op otherwise. Default ``True`` so
    #: memory-enabled agents keep key decisions across multiple compactions.
    flush_before_compaction: bool = True
    max_passes: int = Field(default=3, ge=1)
    #: Coarse trim caps that pre-date L2's summarising compressor —
    #: the existing :class:`DynamicContextMiddleware` (Stream E.3)
    #: still uses these to cut tail history once L2 has folded the
    #: middle. L2 doesn't replace E.3 because the two operate at
    #: different layers (E.3 inside the middleware chain after L2's
    #: preflight). Defaults match the legacy
    #: ``policies.context_compression`` permissive shape (max_turns
    #: 20 / max_tokens 8000) so existing manifests keep loading.
    max_turns: int = Field(default=20, gt=0)
    max_tokens: int = Field(default=8000, gt=0)


class WorkingMemoryPolicy(BaseModel):
    """Stream CM-2 — working-memory sliding-window knobs.

    Drives :class:`~orchestrator.context.working_window.WorkingWindow`, the
    cheap LLM-free gate that runs *before* the L.L2 compressor: when the
    estimated prompt is at/over ``context_window * threshold_pct`` the
    window trims the conversation to the first turn plus the most-recent
    ``max_recent_turns`` user turns (cutting only on ``HumanMessage``
    boundaries so no ToolCall↔ToolResult pair is split). Most light
    overflows are resolved here, sparing the summariser LLM call; the
    compressor only runs on what is still too large afterward.

    Defaults are conservative so existing manifests see **zero behaviour
    change**: a conversation under threshold or at/under
    ``max_recent_turns`` turns is left untouched. Independent of
    :class:`ContextCompressionPolicy` — the two gates compose (window
    first, compressor second) and share the same ``threshold_pct`` shape.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    threshold_pct: float = Field(default=0.7, gt=0.0, le=1.0)
    max_recent_turns: int = Field(default=20, gt=0)
    keep_first_turn: bool = True


class MemoryConsolidationPolicy(BaseModel):
    """Stream Uplift Sprint #7 (Mini-ADR U-39) — per-agent
    MemoryConsolidator knobs.

    The consolidator is a control-plane background worker, not an
    agent-runtime call, but the auxiliary model choice is per-agent
    because different agents may want different durability bars for
    their long-term memory (a customer-support agent may want stricter
    anti-mislearn than a personal-assistant agent).

    ``aux_model`` NULL ↔ consolidator falls back to the platform
    default configured via
    ``HELIX_AGENT_MEMORY_CONSOLIDATOR_DEFAULT_AUX_MODEL`` (see
    control-plane settings).

    Independent of :class:`ContextCompressionPolicy.summariser_model`
    because the two workloads have different cost / latency profiles:
    context compression is hot-path per-turn (favours cheap+fast aux),
    consolidation is cold-path every-4h (can afford a stronger model
    for better anti-mislearn accuracy).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    aux_model: ModelSpec | None = None


class PolicySpec(BaseModel):
    """Tightening to per-field types is deferred to the owning Streams
    (C.5 quota, D.2 PII, E.6 fallback). Permissive dicts now, schemas
    later."""

    model_config = ConfigDict(extra="forbid")

    rate_limit: dict[str, Any] = Field(default_factory=dict)
    pii: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    context_compression: ContextCompressionPolicy = Field(default_factory=ContextCompressionPolicy)
    # Stream CM-2 — working-memory sliding window (cheap pre-compressor
    # gate). Conservative defaults ⇒ zero behaviour change for existing
    # manifests (no-op under threshold / within max_recent_turns).
    working_memory: WorkingMemoryPolicy = Field(default_factory=WorkingMemoryPolicy)
    # Capability Uplift Sprint #7 (Mini-ADR U-39) — per-agent
    # MemoryConsolidator knobs. Defaults are equivalent to "use platform
    # defaults" so existing manifests load unchanged.
    memory_consolidation: MemoryConsolidationPolicy = Field(
        default_factory=MemoryConsolidationPolicy
    )
    trajectory_recording: bool = Field(
        default=True,
        description=(
            "Stream L.L7 — when True, completed runs are serialised to "
            "ObjectStore as ShareGPT-flavoured JSONL for the J.13 eval "
            "gate / future fine-tuning. Set False on agents that must "
            "not leak conversation content to non-WORM storage."
        ),
    )
    run_deadline_s: int = Field(
        default=0,
        ge=0,
        le=86400,
        description=(
            "Mini-ADR J-40 (J.4-补强-2) — wall-clock cap on the whole "
            "run *including any sub-agent recursion*. ``0`` (default) "
            "disables the deadline. When > 0, ``sse.run_agent`` "
            "computes ``deadline_at = time.monotonic() + run_deadline_s`` "
            "once and threads it through config; SubAgentTool checks "
            "the value before each delegation and propagates to child "
            "config unchanged (the child does not reset)."
        ),
    )
    approval_required_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Stream J.8 (Mini-ADR J-24) — declarative approval gate. "
            "Each named tool, when the agent dispatches it, triggers a "
            "LangGraph ``interrupt()`` so a human approves / rejects / "
            "modifies the call before it runs. This is the platform-"
            "enforced path; the agent cannot bypass it. (The agent may "
            "*also* request approval on its own via the ``ask_for_"
            "approval`` builtin — see STREAM-J-DESIGN § 14.5.)"
        ),
    )
    approval_timeout_s: int = Field(
        default=86400,
        ge=60,
        le=604800,
        description=(
            "Stream J.8 (Mini-ADR J-24) — seconds a pending approval "
            "may sit before the timeout job auto-rejects it. Default "
            "24h; an un-actioned approval otherwise pins a checkpointer "
            "slot forever (resource leak)."
        ),
    )


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
    """Enable MCP tools for this agent.

    ``servers`` optionally restricts the agent to the named MCP servers
    (from the platform pool the tenant is allowed to use + the tenant's own
    registered remote servers); empty means every available server. Stream V.
    ``allow_tools`` optionally filters which advertised tools the agent sees
    (by bare tool name, across the selected servers); empty means all."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mcp"] = "mcp"
    servers: list[str] = Field(default_factory=list)
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
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "Stream J.7a — reusable skill bundles this agent enables. "
            "Each element is a string ``name`` (binds latest active "
            "skill version) or ``name@N`` (pins to a specific version row). "
            "Validated against ``SKILL_REF_PATTERN``."
        ),
    )
    triggers: list[TriggerSpec] = Field(
        default_factory=list,
        description=(
            "Stream J.10 — cron / webhook triggers that start runs of "
            "this agent automatically. Reconciled into the agent_trigger "
            "table on deploy (source='manifest')."
        ),
    )
    workflow: WorkflowSpec = Field(default_factory=WorkflowSpec)
    cache: CacheSpec = Field(
        default_factory=CacheSpec,
        description=(
            "Stream K.K4 — LLM response cache toggle. Default enabled; "
            "agents with time-sensitive prompts must set ``enabled: false``."
        ),
    )
    stream_deadline_s: int = Field(
        default=90,
        ge=0,
        le=3600,
        description=(
            "Stream L.L3 — wall-clock cap on a single LLM provider call. "
            "Default 90s (matches Hermes-derived stale-stream timeout). "
            "Hits trigger LLMStreamStaleError so the router falls back to "
            "the next provider rather than locking the run. Set ``0`` to "
            "disable (dev / long-batch paths)."
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

    @model_validator(mode="after")
    def _check_skills(self) -> AgentSpec:
        """J.7a — validate ``spec.skills`` element shape + manifest-local dedup.

        Each element must match ``SKILL_REF_PATTERN`` (``name`` or
        ``name@N``). Same skill name listed twice — even with different
        ``@version`` pins — is a manifest defect; reject.
        """
        from helix_agent.protocol.skill import parse_skill_ref

        seen_names: set[str] = set()
        for raw in self.spec.skills:
            ref = parse_skill_ref(raw)
            if ref.name in seen_names:
                msg = (
                    f"duplicate skill reference {ref.name!r} in skills — "
                    f"a manifest may reference each skill at most once "
                    f"(use a single pin or no pin, not both)."
                )
                raise ValueError(msg)
            seen_names.add(ref.name)
        return self

    @model_validator(mode="after")
    def _check_triggers(self) -> AgentSpec:
        """J.10 — reject duplicate trigger names within the manifest.

        Trigger names are the manifest-reconciliation key
        ``(tenant, agent, name)``; two ``triggers:`` entries sharing a
        name would collide on deploy.
        """
        seen: set[str] = set()
        for trig in self.spec.triggers:
            if trig.name in seen:
                msg = f"duplicate trigger name {trig.name!r} in triggers."
                raise ValueError(msg)
            seen.add(trig.name)
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
