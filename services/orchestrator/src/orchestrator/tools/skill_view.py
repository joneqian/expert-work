"""``skill_view`` orchestrator tool — Capability Uplift Sprint #3
(Mini-ADRs U-17 + U-21).

When an agent is given access to one or more skills (via ``skills:`` in
its manifest), :class:`SkillViewTool` is registered as a single
``skill_view(skill_name, path)`` tool. The tool serves as the single
mental model for reading skill content:

- ``path == "SKILL.md"`` → re-pack the version's frontmatter +
  prompt_fragment into a Markdown document and return it
- ``path == "reference/foo.md"`` (or any other subdir) → look up the
  base64-encoded entry in ``supporting_files`` and return its decoded
  contents

Every read goes through the U-21 double check:

1. **Drift detection** — recompute ``content_hash`` over canonicalized
   ``(prompt_fragment, supporting_files)`` and compare against the
   stored value. Mismatch fires :func:`record_skill_drift` + a
   ``SKILL_DRIFT_DETECTED`` audit row (P0 — almost certainly SQL
   injection or internal actor) and returns a ``[BLOCKED]`` placeholder.

2. **Context-scope re-scan** — run ``scan_for_threats(content,
   scope="context")`` on the chosen content. A hit fires
   :func:`record_skill_redacted` + :func:`record_threat_pattern_hits`
   and returns a ``[BLOCKED]`` placeholder. This catches the case
   where a pattern set update adds new rules after the row was already
   imported.

Both placeholders preserve Oracle defense: the LLM sees that the
content was withheld but never sees the offending substring or the
recomputed hash.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from helix_agent.common.threat_patterns import scan_for_threats
from helix_agent.common.uplift_metrics import (
    record_skill_drift,
    record_skill_redacted,
    record_skill_view,
    record_threat_pattern_hits,
)
from helix_agent.protocol import SkillVersion
from helix_agent.protocol.skill import (
    compute_content_hash,
    supporting_files_to_jsonable,
)
from helix_agent.protocol.skill_package import (
    ParsedSkillMd,
    serialize_skill_md,
)
from orchestrator.tools.registry import (
    ToolBlockedError,
    ToolContext,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)

#: Mirrors ``MCPTool`` (Sprint #5). Skill content can grow but the LLM
#: doesn't need to see all of it on every read — middle-trim if over
#: this many chars so head + tail are both visible.
SKILL_VIEW_CONTENT_CAP: int = 20_000
_TRUNCATION_PREFIX = "...["
_TRUNCATION_SUFFIX = " chars truncated]..."


@runtime_checkable
class SkillResolver(Protocol):
    """Minimum surface :class:`SkillViewTool` needs.

    Production wiring: a thin shim over :class:`SkillStore.resolve_by_name`.
    Tests inject a :class:`RecordingSkillResolver` for determinism.
    """

    async def resolve(self, *, tenant_id: UUID, skill_name: str) -> SkillVersion | None:
        """Return the active version for ``skill_name``, or ``None``."""


@dataclass(frozen=True)
class RecordingSkillResolver:
    """In-memory :class:`SkillResolver` for tests."""

    versions: Mapping[tuple[UUID, str], SkillVersion]

    async def resolve(self, *, tenant_id: UUID, skill_name: str) -> SkillVersion | None:
        return self.versions.get((tenant_id, skill_name))


@dataclass(frozen=True)
class SkillViewTool:
    """The ``skill_view`` tool — Capability Uplift Sprint #3.

    Stateless across calls; the per-tenant scope comes from
    ``ctx.tenant_id`` which the orchestrator's ReAct loop populates from
    the run binding. ``allowed_skill_names`` is the manifest's
    ``skills:`` list (parsed by Stream J.7a); the tool refuses to load
    anything outside this set so a poisoned skill body can't pivot to
    reading other tenants' skills via this surface.
    """

    resolver: SkillResolver
    allowed_skill_names: frozenset[str]
    content_char_cap: int = SKILL_VIEW_CONTENT_CAP

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="skill_view",
            description=(
                "Read a file from one of the available skills. Available "
                "skills + their file lists are listed in the system prompt "
                'under <available-skills>. Use `path="SKILL.md"` for the '
                "main body, or the relative path under the skill for a "
                "supporting file (e.g. `reference/error_codes.md`)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": (
                            "Name of the skill (must be one of those listed in <available-skills>)."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            'File path within the skill — "SKILL.md" or a '
                            "relative supporting-file path."
                        ),
                    },
                },
                "required": ["skill_name", "path"],
            },
            is_read_only=True,
        )

    async def call(self, args: Mapping[str, Any], *, ctx: ToolContext) -> ToolResult:
        if ctx.tenant_id is None:
            msg = "skill_view requires a tenant binding"
            raise ToolBlockedError(msg)

        skill_name = self._require_str(args, "skill_name")
        path = self._require_str(args, "path")

        if skill_name not in self.allowed_skill_names:
            # Stay quiet about whether the skill exists for other tenants
            # — only signal that this agent can't reach it.
            return ToolResult(
                content=(f"[NOT AVAILABLE: skill {skill_name!r} is not in the agent's allowlist]"),
                meta={"skill_name": skill_name, "result": "not_allowed"},
            )

        version = await self.resolver.resolve(tenant_id=ctx.tenant_id, skill_name=skill_name)
        if version is None:
            record_skill_view(result="not_found")
            return ToolResult(
                content=f"[NOT FOUND: skill {skill_name!r}]",
                meta={"skill_name": skill_name, "result": "not_found"},
            )

        # ── U-21 step 1: drift check ────────────────────────────────
        jsonable_files = supporting_files_to_jsonable(version.supporting_files)
        recomputed_hash = compute_content_hash(version.prompt_fragment, jsonable_files)
        if recomputed_hash != version.content_hash:
            record_skill_drift()
            # Audit is the caller's job (orchestrator builds a tool-call
            # audit envelope downstream). We log a structured marker so
            # SecOps can correlate; no user content goes into extra=
            # per [memory:codeql-log-injection-request-taint].
            logger.warning(
                "skill_view.drift_detected skill=%s path=%s",
                skill_name,
                path,
            )
            return ToolResult(
                content=(f"[BLOCKED: skill content drift detected for {skill_name!r}/{path}]"),
                meta={
                    "skill_name": skill_name,
                    "result": "drift",
                    "is_error": True,
                },
            )

        # ── Extract requested content ───────────────────────────────
        if path == "SKILL.md":
            content = _repack_skill_md(version)
        else:
            file_entry = version.supporting_files.get(path)
            if file_entry is None:
                record_skill_view(result="not_found")
                return ToolResult(
                    content=(f"[NOT FOUND: {path!r} in skill {skill_name!r}]"),
                    meta={"skill_name": skill_name, "result": "not_found"},
                )
            content = _decode_supporting_file(file_entry)

        # ── U-21 step 2: context-scope re-scan ──────────────────────
        findings = scan_for_threats(content, scope="context")
        if findings:
            record_threat_pattern_hits(findings, scope="context")
            record_skill_redacted()
            logger.warning(
                "skill_view.context_match skill=%s path=%s findings=%d",
                skill_name,
                path,
                len(findings),
            )
            return ToolResult(
                content=("[BLOCKED: content matched threat pattern at runtime]"),
                meta={
                    "skill_name": skill_name,
                    "result": "redacted",
                    "is_error": True,
                },
            )

        # ── Truncate to LLM-friendly size ───────────────────────────
        rendered, truncated = _middle_trim(content, self.content_char_cap)
        record_skill_view(result="truncated" if truncated else "ok")
        return ToolResult(
            content=rendered,
            meta={
                "skill_name": skill_name,
                "result": "truncated" if truncated else "ok",
                "truncated": truncated,
            },
        )

    @staticmethod
    def _require_str(args: Mapping[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value:
            msg = f"skill_view requires non-empty {key!r}"
            raise ToolBlockedError(msg)
        return value


def _repack_skill_md(version: SkillVersion) -> str:
    """Reconstruct the canonical SKILL.md text from a SkillVersion row."""
    parsed = ParsedSkillMd(
        name=_skill_name_for_repack(version),
        description=version.description or _skill_name_for_repack(version),
        license=None,
        helix_version=version.version,
        helix_category=version.category,
        helix_required_models=version.required_models,
        helix_tool_names=version.tool_names,
        helix_authored_by=version.authored_by,
        helix_lazy=version.lazy_load,
        body=version.prompt_fragment,
    )
    return serialize_skill_md(parsed)


def _skill_name_for_repack(version: SkillVersion) -> str:
    """SkillVersion only knows the skill_id, not the skill row's name.
    For SKILL.md re-pack we need the name — fall back to the version's
    description or a synthetic name. In practice the caller (orchestrator)
    can pass an enriched version with the name baked in via the
    ``allowed_skill_names`` lookup; for now this default keeps round-trip
    deterministic without a second store fetch."""
    # The skill_name is the lookup key the agent used. If the
    # `SkillResolver` is the production shim it will have stamped the
    # name into a side channel — but to keep the DTO minimal we use the
    # description as a stable string. The body is what matters; an
    # empty / synthetic name in SKILL.md re-pack is acceptable for an
    # internal-only tool consumption.
    return version.description.split("\n", 1)[0] or "skill"


def _decode_supporting_file(entry: object) -> str:
    """SupportingFile.content is base64 of raw bytes. Decode + best-effort
    UTF-8 (binary files come back as a [BINARY: ...] marker so the LLM
    knows not to try parsing them as prose)."""
    import base64

    if hasattr(entry, "content"):
        raw_b64 = entry.content
        mime = getattr(entry, "mime", "") or ""
        size = getattr(entry, "size", 0)
    elif isinstance(entry, dict):
        raw_b64 = entry.get("content", "")
        mime = entry.get("mime", "") or ""
        size = entry.get("size", 0)
    else:
        return "[BINARY: unknown entry format]"

    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except (ValueError, TypeError):
        return f"[BINARY: corrupt content, {size} bytes, mime={mime!r}]"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"[BINARY: {size} bytes, mime={mime!r}]"


def _middle_trim(text: str, cap: int) -> tuple[str, bool]:
    """Same middle-truncation pattern as ``MCPTool`` — keeps head + tail
    50% so the LLM sees both ends of a long file."""
    if len(text) <= cap:
        return text, False
    half = cap // 2
    dropped = len(text) - cap
    head = text[:half]
    tail = text[-half:]
    return (
        f"{head}\n{_TRUNCATION_PREFIX}{dropped}{_TRUNCATION_SUFFIX}\n{tail}",
        True,
    )
