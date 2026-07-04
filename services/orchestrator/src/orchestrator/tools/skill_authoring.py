"""In-session agent skill authoring builtins — Stream SE (SE-3b/SE-3c, Layer A).

Four builtins let an agent grow its own skill library *during a run*
(the deer-flow / hermes ``skill_manage`` equivalent, split into clear
verbs per J.7b-1 §15.7):

* ``author_skill``            — create a brand-new skill from scratch.
* ``refine_skill``            — append an improved version to a skill it owns.
* ``fork_skill``              — copy any visible skill into a new agent-private one.
* ``propose_skill_to_tenant`` — request promotion of an owned agent_private skill
  to tenant-wide visibility (SE-3c). Opens a review request (SE-8 governance);
  it does NOT promote — a tenant admin / system_admin approves.

Everything an agent produces is **DRAFT + agent_private** (owner = the
per-user persistent agent = ``(tenant_id, user_id, agent_name)``):

* DRAFT means it is NOT bound by bare-name resolution — it cannot affect
  any run until it is activated, which only happens via the U-24 publish
  gate (admin) or the SE-7 governance gate. These tools never activate.
* agent_private means it is owned by *this user's this agent*; cross-agent
  / cross-user / tenant sharing is a separate, gated step
  (``propose_skill_to_tenant`` → SE-7).

Write-time content is threat-scanned (U-22, ``strict`` scope) and
``high_risk`` is computed (U-24) so activation later routes through human
review. ``tenant_id`` + ``user_id`` come from the per-call
:class:`ToolContext`; ``agent_name`` is baked at build time (it is stable
across manifest versions — the owner key).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from helix_agent.common.threat_patterns import first_threat_message
from helix_agent.persistence.skill.base import (
    DuplicatePromoteRequestError,
    DuplicateSkillError,
    SkillNotFoundError,
    SkillStore,
    SkillVersionNotFoundError,
)
from helix_agent.protocol import AuditAction, AuditEntry, AuditResult
from helix_agent.protocol.skill import (
    DEFAULT_SKILL_LAZY_LOAD,
    compute_content_hash,
    is_high_risk_skill_version,
)
from helix_agent.runtime.audit.logger import AuditLogger
from orchestrator.tools.registry import ToolContext, ToolResult, ToolSpec

#: Skill name slug (mirrors ``SKILL_REF_PATTERN`` minus the ``@version`` part).
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: The builtin names this module backs. ``agent_factory.build_agent``
#: registers the matching tool objects (it alone has ``agent_name`` + the
#: ``SkillStore``); ``assembly._register_builtin`` treats them as no-ops.
SKILL_AUTHORING_BUILTINS: frozenset[str] = frozenset(
    {
        "author_skill",
        "refine_skill",
        "fork_skill",
        "propose_skill_to_tenant",
        # Stream SE — SE-10 text-class harness component authoring.
        "note_behavior_patch",
        "clarify_tool_usage",
        "remember",
    }
)


def _require(ctx: ToolContext) -> tuple[UUID, UUID]:
    """Return ``(tenant_id, user_id)`` or raise — authoring needs both."""
    if ctx.tenant_id is None:
        raise ValueError("skill authoring requires a tenant-scoped run")
    if ctx.user_id is None:
        raise ValueError("skill authoring requires a user-bound run (owner identity)")
    return ctx.tenant_id, ctx.user_id


def _block_if_threat(prompt_fragment: str) -> ToolResult | None:
    """Write-time U-22 strict scan. Returns a blocking ToolResult or None."""
    msg = first_threat_message(prompt_fragment, scope="strict")
    if msg is not None:
        return ToolResult(
            content=f"[BLOCKED: skill content failed the safety scan: {msg}]",
            meta={"result": "blocked_threat", "is_error": True},
        )
    return None


async def _emit(
    audit_logger: AuditLogger | None,
    ctx: ToolContext,
    *,
    action: AuditAction,
    tenant_id: UUID,
    skill_id: UUID,
    details: dict[str, Any],
) -> None:
    if audit_logger is None:
        return
    await audit_logger.write(
        AuditEntry(
            tenant_id=tenant_id,
            actor_type="agent",
            actor_id=str(ctx.run_id) if ctx.run_id is not None else "agent",
            action=action,
            resource_type="skill",
            resource_id=str(skill_id),
            result=AuditResult.SUCCESS,
            details=details,
        )
    )


def _tool_names(args: Mapping[str, Any]) -> tuple[str, ...]:
    raw = args.get("tool_names") or ()
    if not isinstance(raw, list | tuple):
        raise ValueError("tool_names must be a list of strings")
    return tuple(str(t) for t in raw)


@dataclass(frozen=True)
class AuthorSkillTool:
    """``author_skill`` — create a new DRAFT, agent_private skill."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="author_skill",
            description=(
                "Create a new reusable skill from scratch when you discover a "
                "non-trivial, repeatable workflow worth remembering. The skill "
                "is saved as a DRAFT private to you (this agent); it does not "
                "affect any run until an admin reviews and activates it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "hyphen-case slug, ^[a-z][a-z0-9_-]{0,63}$",
                    },
                    "description": {"type": "string", "description": "one-line summary"},
                    "prompt_fragment": {
                        "type": "string",
                        "description": "the skill body (markdown) — how to do this class of task",
                    },
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "optional tool subset this skill activates",
                    },
                },
                "required": ["name", "description", "prompt_fragment"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = _require(ctx)
        name = str(args.get("name", "")).strip()
        if not _SKILL_NAME_RE.match(name):
            raise ValueError(f"invalid skill name {name!r}: must match ^[a-z][a-z0-9_-]{{0,63}}$")
        description = str(args.get("description", "")).strip()
        prompt_fragment = str(args.get("prompt_fragment", ""))
        if not prompt_fragment.strip():
            raise ValueError("prompt_fragment must not be empty")
        tool_names = _tool_names(args)

        blocked = _block_if_threat(prompt_fragment)
        if blocked is not None:
            return blocked

        high_risk = is_high_risk_skill_version(tool_names=tool_names, supporting_file_paths=[])
        content_hash = compute_content_hash(prompt_fragment, None)
        skill_id = uuid4()
        try:
            await self.store.create_skill(
                skill_id=skill_id,
                tenant_id=tenant_id,
                name=name,
                description=description,
                visibility="agent_private",
                created_by_user_id=user_id,
                created_by_agent_name=self.agent_name,
            )
        except DuplicateSkillError:
            return ToolResult(
                content=(
                    f"[A skill named {name!r} already exists in this tenant. "
                    f"Use refine_skill if it is yours, or pick another name.]"
                ),
                meta={"result": "duplicate", "is_error": True},
            )
        version = await self.store.add_version(
            version_id=uuid4(),
            skill_id=skill_id,
            tenant_id=tenant_id,
            prompt_fragment=prompt_fragment,
            tool_names=tool_names,
            description=description,
            authored_by="agent",
            content_hash=content_hash,
            high_risk=high_risk,
            evolution_origin="in_session",
        )
        await _emit(
            self.audit_logger,
            ctx,
            action=AuditAction.SKILL_AUTHORED_BY_AGENT,
            tenant_id=tenant_id,
            skill_id=skill_id,
            details={"version": version.version, "high_risk": high_risk},
        )
        return ToolResult(
            content=(
                f"Authored skill {name!r} as v{version.version} (DRAFT, agent_private). "
                f"It will not be used until an admin activates it"
                f"{' — flagged high-risk, requires review' if high_risk else ''}."
            ),
            meta={
                "result": "ok",
                "skill_name": name,
                "skill_id": str(skill_id),
                "version": version.version,
                "high_risk": high_risk,
            },
        )


@dataclass(frozen=True)
class RefineSkillTool:
    """``refine_skill`` — append an improved version to a skill this agent owns."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="refine_skill",
            description=(
                "Improve a skill you previously authored by saving a new version "
                "(e.g. after a correction or a better approach emerged). You can "
                "only refine skills you own. Saves a new DRAFT version."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "name of the skill to refine"},
                    "prompt_fragment": {
                        "type": "string",
                        "description": "the improved skill body (replaces the prior version's)",
                    },
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "optional updated tool subset",
                    },
                },
                "required": ["name", "prompt_fragment"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = _require(ctx)
        name = str(args.get("name", "")).strip()
        prompt_fragment = str(args.get("prompt_fragment", ""))
        if not prompt_fragment.strip():
            raise ValueError("prompt_fragment must not be empty")
        tool_names = _tool_names(args)

        skill = await self.store.get_skill_by_name(tenant_id=tenant_id, name=name)
        if skill is None:
            return ToolResult(
                content=f"[No skill named {name!r} found.]",
                meta={"result": "not_found", "is_error": True},
            )
        if not (
            skill.created_by_user_id == user_id and skill.created_by_agent_name == self.agent_name
        ):
            return ToolResult(
                content=(
                    f"[Skill {name!r} is not yours to refine. Use fork_skill to "
                    f"make your own copy first.]"
                ),
                meta={"result": "forbidden", "is_error": True},
            )

        blocked = _block_if_threat(prompt_fragment)
        if blocked is not None:
            return blocked

        high_risk = is_high_risk_skill_version(tool_names=tool_names, supporting_file_paths=[])
        # RT-ADR-11 — refining an existing skill keeps its disclosure mode; only
        # brand-new skills adopt the lazy default (no prior version → default).
        prior_versions = await self.store.list_versions(skill_id=skill.id, tenant_id=tenant_id)
        inherited_lazy = prior_versions[0].lazy_load if prior_versions else DEFAULT_SKILL_LAZY_LOAD
        version = await self.store.add_version(
            version_id=uuid4(),
            skill_id=skill.id,
            tenant_id=tenant_id,
            prompt_fragment=prompt_fragment,
            tool_names=tool_names,
            description=skill.description,
            authored_by="agent",
            lazy_load=inherited_lazy,
            content_hash=compute_content_hash(prompt_fragment, None),
            high_risk=high_risk,
            evolution_origin="in_session",
        )
        await _emit(
            self.audit_logger,
            ctx,
            action=AuditAction.SKILL_REFINED_BY_AGENT,
            tenant_id=tenant_id,
            skill_id=skill.id,
            details={"version": version.version, "high_risk": high_risk},
        )
        return ToolResult(
            content=f"Refined skill {name!r} → v{version.version} (DRAFT). Pending review.",
            meta={
                "result": "ok",
                "skill_name": name,
                "skill_id": str(skill.id),
                "version": version.version,
                "high_risk": high_risk,
            },
        )


@dataclass(frozen=True)
class ForkSkillTool:
    """``fork_skill`` — copy a visible skill into a new agent-private one."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="fork_skill",
            description=(
                "Make your own private copy of an existing skill so you can adapt "
                "it (then refine_skill the copy). The fork is a DRAFT private to you."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_name": {"type": "string", "description": "skill to fork"},
                    "new_name": {
                        "type": "string",
                        "description": "name for your copy, ^[a-z][a-z0-9_-]{0,63}$",
                    },
                },
                "required": ["source_name", "new_name"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = _require(ctx)
        source_name = str(args.get("source_name", "")).strip()
        new_name = str(args.get("new_name", "")).strip()
        if not _SKILL_NAME_RE.match(new_name):
            raise ValueError(f"invalid new_name {new_name!r}: must match ^[a-z][a-z0-9_-]{{0,63}}$")
        source = await self.store.get_skill_by_name(tenant_id=tenant_id, name=source_name)
        if source is None:
            return ToolResult(
                content=f"[No skill named {source_name!r} found to fork.]",
                meta={"result": "not_found", "is_error": True},
            )
        try:
            forked = await self.store.fork_skill(
                tenant_id=tenant_id,
                source_skill_id=source.id,
                new_name=new_name,
                by_user_id=user_id,
                by_agent_name=self.agent_name,
                new_skill_id=uuid4(),
                new_version_id=uuid4(),
            )
        except DuplicateSkillError:
            return ToolResult(
                content=f"[A skill named {new_name!r} already exists. Pick another name.]",
                meta={"result": "duplicate", "is_error": True},
            )
        except (SkillNotFoundError, SkillVersionNotFoundError):
            return ToolResult(
                content=f"[Skill {source_name!r} has no published version to fork yet.]",
                meta={"result": "not_found", "is_error": True},
            )
        await _emit(
            self.audit_logger,
            ctx,
            action=AuditAction.SKILL_FORKED_BY_AGENT,
            tenant_id=tenant_id,
            skill_id=forked.id,
            details={"forked_from": str(source.id), "new_name": new_name},
        )
        return ToolResult(
            content=(
                f"Forked {source_name!r} → {new_name!r} (DRAFT, agent_private, v1). "
                f"Use refine_skill {new_name!r} to adapt it."
            ),
            meta={
                "result": "ok",
                "skill_name": new_name,
                "skill_id": str(forked.id),
                "forked_from": str(source.id),
            },
        )


@dataclass(frozen=True)
class ProposeSkillToTenantTool:
    """``propose_skill_to_tenant`` — request agent_private→tenant promotion (SE-3c).

    Opens a review request (SE-8 ``skill_promote_request``) for a skill this
    agent owns. It does NOT change visibility — a tenant admin / system_admin
    approves (or rejects) via the governance surface. The agent can only propose
    its own agent_private skills, and at most one request stays pending per skill.
    """

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="propose_skill_to_tenant",
            description=(
                "Request that a skill you authored be shared tenant-wide. This "
                "opens a review request for an admin to approve — it does NOT "
                "activate or share the skill by itself. Use it when a private "
                "skill has proven broadly useful."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "name of the skill to propose"},
                    "reason": {
                        "type": "string",
                        "description": "why it is worth sharing tenant-wide (for the reviewer)",
                    },
                },
                "required": ["name"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        tenant_id, user_id = _require(ctx)
        name = str(args.get("name", "")).strip()
        reason = str(args.get("reason", "")).strip()

        skill = await self.store.get_skill_by_name(tenant_id=tenant_id, name=name)
        if skill is None:
            return ToolResult(
                content=f"[No skill named {name!r} found.]",
                meta={"result": "not_found", "is_error": True},
            )
        if not (
            skill.created_by_user_id == user_id and skill.created_by_agent_name == self.agent_name
        ):
            return ToolResult(
                content=f"[Skill {name!r} is not yours to propose.]",
                meta={"result": "forbidden", "is_error": True},
            )
        if skill.visibility == "tenant":
            return ToolResult(
                content=f"[Skill {name!r} is already tenant-wide; nothing to propose.]",
                meta={"result": "already_tenant"},
            )
        if skill.latest_version < 1:
            return ToolResult(
                content=f"[Skill {name!r} has no published version to propose yet.]",
                meta={"result": "no_version", "is_error": True},
            )

        try:
            request = await self.store.request_skill_promote(
                request_id=uuid4(),
                tenant_id=tenant_id,
                skill_id=skill.id,
                skill_version=skill.latest_version,
                requested_by_user_id=user_id,
                requested_by_agent_name=self.agent_name,
                reason=reason,
            )
        except DuplicatePromoteRequestError:
            return ToolResult(
                content=f"[A promotion request for {name!r} is already pending review.]",
                meta={"result": "already_pending"},
            )

        if self.audit_logger is not None:
            await self.audit_logger.write(
                AuditEntry(
                    tenant_id=tenant_id,
                    actor_type="agent",
                    actor_id=str(ctx.run_id) if ctx.run_id is not None else "agent",
                    action=AuditAction.SKILL_PROMOTE_REQUESTED,
                    resource_type="skill_promote_request",
                    resource_id=str(request.id),
                    result=AuditResult.SUCCESS,
                    details={"skill_id": str(skill.id), "skill_version": skill.latest_version},
                )
            )
        return ToolResult(
            content=(
                f"Requested promotion of {name!r} (v{skill.latest_version}) to tenant-wide. "
                f"An admin will review it; it stays private until approved."
            ),
            meta={
                "result": "ok",
                "skill_name": name,
                "skill_id": str(skill.id),
                "request_id": str(request.id),
            },
        )


# ── Stream SE — SE-10 text-class harness components (Mini-ADR SE-A15) ──────
# Three builtins author the no-execution-risk text components, reusing the
# AuthorSkillTool discipline (threat scan / DRAFT / agent_private / provenance
# / audit). They carry no tools, so they are never high-risk. ``component_type``
# is set on the parent skill; activation later still flows through the same
# replay-validation + governance gate as a plain skill (SE-A0 unchanged).


async def _author_text_component(
    *,
    store: SkillStore,
    agent_name: str,
    audit_logger: AuditLogger | None,
    ctx: ToolContext,
    name: str,
    description: str,
    prompt_fragment: str,
    component_type: str,
    target_tool_name: str | None,
    noun: str,
) -> ToolResult:
    """Shared create-path for the three text-component authoring builtins."""
    tenant_id, user_id = _require(ctx)
    if not _SKILL_NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r}: must match ^[a-z][a-z0-9_-]{{0,63}}$")
    if not prompt_fragment.strip():
        raise ValueError("prompt_fragment must not be empty")

    blocked = _block_if_threat(prompt_fragment)
    if blocked is not None:
        return blocked

    skill_id = uuid4()
    try:
        await store.create_skill(
            skill_id=skill_id,
            tenant_id=tenant_id,
            name=name,
            description=description or name,
            visibility="agent_private",
            created_by_user_id=user_id,
            created_by_agent_name=agent_name,
            component_type=component_type,  # type: ignore[arg-type]
            target_tool_name=target_tool_name,
        )
    except DuplicateSkillError:
        return ToolResult(
            content=f"[A skill/component named {name!r} already exists. Pick another name.]",
            meta={"result": "duplicate", "is_error": True},
        )
    version = await store.add_version(
        version_id=uuid4(),
        skill_id=skill_id,
        tenant_id=tenant_id,
        prompt_fragment=prompt_fragment,
        tool_names=(),
        description=description or name,
        authored_by="agent",
        content_hash=compute_content_hash(prompt_fragment, None),
        high_risk=False,
        evolution_origin="in_session",
    )
    await _emit(
        audit_logger,
        ctx,
        action=AuditAction.SKILL_AUTHORED_BY_AGENT,
        tenant_id=tenant_id,
        skill_id=skill_id,
        details={"version": version.version, "component_type": component_type},
    )
    return ToolResult(
        content=(
            f"Saved {noun} {name!r} as v{version.version} (DRAFT, agent_private). "
            f"It will not be used until validated + activated."
        ),
        meta={
            "result": "ok",
            "skill_name": name,
            "skill_id": str(skill_id),
            "version": version.version,
            "component_type": component_type,
        },
    )


@dataclass(frozen=True)
class NoteBehaviorPatchTool:
    """``note_behavior_patch`` — author an agent-level behavior refinement."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="note_behavior_patch",
            description=(
                "Record a reusable behavior refinement for yourself (e.g. 'for "
                "reconciliation tasks, list the verification checklist first'). "
                "Saved as a DRAFT private behavior patch; it does not affect any "
                "run until validated and activated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "slug, ^[a-z][a-z0-9_-]{0,63}$"},
                    "description": {"type": "string", "description": "one-line summary (optional)"},
                    "prompt_fragment": {
                        "type": "string",
                        "description": "the behavior refinement (markdown)",
                    },
                },
                "required": ["name", "prompt_fragment"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        return await _author_text_component(
            store=self.store,
            agent_name=self.agent_name,
            audit_logger=self.audit_logger,
            ctx=ctx,
            name=str(args.get("name", "")).strip(),
            description=str(args.get("description", "")).strip(),
            prompt_fragment=str(args.get("prompt_fragment", "")),
            component_type="system_prompt",
            target_tool_name=None,
            noun="behavior patch",
        )


@dataclass(frozen=True)
class ClarifyToolUsageTool:
    """``clarify_tool_usage`` — author a usage note for an already-bound tool."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="clarify_tool_usage",
            description=(
                "Record a usage clarification for one of your tools (e.g. a "
                "caveat or example). Text only — it never changes the tool's "
                "parameters or behavior. Saved as a DRAFT private note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "slug, ^[a-z][a-z0-9_-]{0,63}$"},
                    "target_tool_name": {
                        "type": "string",
                        "description": "the tool this note clarifies",
                    },
                    "description": {"type": "string", "description": "one-line summary (optional)"},
                    "prompt_fragment": {
                        "type": "string",
                        "description": "the usage clarification (markdown)",
                    },
                },
                "required": ["name", "target_tool_name", "prompt_fragment"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        target = str(args.get("target_tool_name", "")).strip()
        if not target:
            raise ValueError("target_tool_name must not be empty")
        return await _author_text_component(
            store=self.store,
            agent_name=self.agent_name,
            audit_logger=self.audit_logger,
            ctx=ctx,
            name=str(args.get("name", "")).strip(),
            description=str(args.get("description", "")).strip(),
            prompt_fragment=str(args.get("prompt_fragment", "")),
            component_type="tool_description",
            target_tool_name=target,
            noun="tool note",
        )


@dataclass(frozen=True)
class RememberTool:
    """``remember`` — author a reusable long-term memory entry."""

    store: SkillStore
    agent_name: str
    audit_logger: AuditLogger | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "Record a durable fact or preference to carry across sessions "
                "(e.g. 'this user's reports use calendar-month periods'). Saved "
                "as a DRAFT private memory entry; activated after validation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "slug, ^[a-z][a-z0-9_-]{0,63}$"},
                    "description": {"type": "string", "description": "one-line summary (optional)"},
                    "prompt_fragment": {
                        "type": "string",
                        "description": "the fact/preference to remember (markdown)",
                    },
                },
                "required": ["name", "prompt_fragment"],
            },
            is_read_only=False,
            side_effect="reversible",
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        return await _author_text_component(
            store=self.store,
            agent_name=self.agent_name,
            audit_logger=self.audit_logger,
            ctx=ctx,
            name=str(args.get("name", "")).strip(),
            description=str(args.get("description", "")).strip(),
            prompt_fragment=str(args.get("prompt_fragment", "")),
            component_type="memory_entry",
            target_tool_name=None,
            noun="memory entry",
        )


_AuthoringTool = (
    AuthorSkillTool
    | RefineSkillTool
    | ForkSkillTool
    | ProposeSkillToTenantTool
    | NoteBehaviorPatchTool
    | ClarifyToolUsageTool
    | RememberTool
)


def build_skill_authoring_tools(
    *,
    declared: Sequence[str],
    store: SkillStore,
    agent_name: str,
    audit_logger: AuditLogger | None,
) -> list[_AuthoringTool]:
    """Build the authoring tool objects the manifest declared.

    ``declared`` is the set of builtin names the manifest's ``tools:`` block
    listed (intersected with :data:`SKILL_AUTHORING_BUILTINS` by the caller).
    """
    tools: list[_AuthoringTool] = []
    wanted = set(declared)
    if "author_skill" in wanted:
        tools.append(AuthorSkillTool(store=store, agent_name=agent_name, audit_logger=audit_logger))
    if "refine_skill" in wanted:
        tools.append(RefineSkillTool(store=store, agent_name=agent_name, audit_logger=audit_logger))
    if "fork_skill" in wanted:
        tools.append(ForkSkillTool(store=store, agent_name=agent_name, audit_logger=audit_logger))
    if "propose_skill_to_tenant" in wanted:
        tools.append(
            ProposeSkillToTenantTool(store=store, agent_name=agent_name, audit_logger=audit_logger)
        )
    if "note_behavior_patch" in wanted:
        tools.append(
            NoteBehaviorPatchTool(store=store, agent_name=agent_name, audit_logger=audit_logger)
        )
    if "clarify_tool_usage" in wanted:
        tools.append(
            ClarifyToolUsageTool(store=store, agent_name=agent_name, audit_logger=audit_logger)
        )
    if "remember" in wanted:
        tools.append(RememberTool(store=store, agent_name=agent_name, audit_logger=audit_logger))
    return tools
