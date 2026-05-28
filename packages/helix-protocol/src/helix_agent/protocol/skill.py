"""J.7a Skill 静态启用 — DTOs.

Mini-ADR J-23 + 2026-05-21 修订 (STREAM-J-DESIGN § 15). M0 = prompt
片段 + tools 子集 (不含 code 字段)+ 版本化 + draft 闸门 + admin
CRUD API + ZIP import/export + ``name@version`` 版本固定.

These DTOs are the wire shape between control-plane (admin API),
orchestrator (skill loader at build time), and helix-persistence
(``SkillStore`` ABC).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "HIGH_RISK_TOOLS",
    "Skill",
    "SkillAuthoredBy",
    "SkillPackageLayoutError",
    "SkillRef",
    "SkillStatus",
    "SkillSupportingFile",
    "SkillVersion",
    "canonicalize_skill_content",
    "compute_content_hash",
    "is_high_risk_skill_version",
    "parse_skill_ref",
    "supporting_files_to_jsonable",
]


# ── Capability Uplift Sprint #3 (Mini-ADR U-24) ──────────────────────────
# Tools that escalate a skill's blast-radius to "needs human review before
# activate". Includes any tool that lets the skill execute arbitrary code
# (exec_python / exec_shell) or make uncontrolled network egress (http).
# A skill with any of these in ``tool_names`` flips ``high_risk = True``
# and the publish gate at PATCH /v1/skills/{id} status=active rejects
# non-admin actors.
HIGH_RISK_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "exec_python",
        "exec_shell",
        "http",
    }
)


class SkillPackageLayoutError(ValueError):
    """Raised by ZIP / SKILL.md parsers when the input is structurally
    invalid (missing SKILL.md / bad path / banned extension / etc).

    The control-plane layer catches this and returns a **generic** 400
    so attackers don't get an oracle that reveals which check fired
    (Mini-ADR U-18 / U-21 Oracle defense). The full violation detail
    is recorded in the audit row for SecOps triage.
    """


class SkillStatus(StrEnum):
    """``skill.status`` lifecycle states.

    ``DRAFT`` — newly created or freshly re-authored; not visible to a
    manifest that references the skill by bare ``name`` (only pinned
    ``name@version`` lookups can see draft).

    ``ACTIVE`` — current published version; bare ``name`` references
    resolve to ``skill.latest_version`` (which must be active when the
    skill is in this state).

    ``ARCHIVED`` — retired skills; bare ``name`` references reject at
    build time but historical pinned ``name@N`` references still resolve
    (reproducibility — agents pinned to old versions keep working).
    """

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


SkillAuthoredBy = Literal["human", "agent"]


class SkillVersion(BaseModel):
    """One row of ``skill_version`` — an immutable published version.

    ``prompt_fragment`` is Markdown-multipara allowed (the loader wraps
    it in ``<skill name="..." version="...">...</skill>`` before
    splicing into the agent's system prompt — see Mini-ADR J-23 §
    15.6 (c) 红线).

    ``tool_names`` is the tool subset the skill activates. The build-
    time merger rejects a manifest whose two skills declare overlapping
    tool names (``SkillConflictError``).

    ``required_models`` — when non-empty, the agent's primary
    ``model.name`` MUST appear in this list or the build fails. Empty
    means "no compatibility constraint".
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    skill_id: UUID
    tenant_id: UUID
    version: int = Field(ge=1)
    prompt_fragment: str
    tool_names: tuple[str, ...] = ()
    description: str = ""
    category: str | None = None
    required_models: tuple[str, ...] = ()
    authored_by: SkillAuthoredBy = "human"
    # Capability Uplift Sprint #3 (Mini-ADR U-16) — supporting files
    # under arbitrary subdirectories. Map of path → SkillSupportingFile.
    # The DB column is JSONB; this DTO uses ``dict`` rather than
    # ``Mapping`` so Pydantic can construct from raw JSON payloads.
    supporting_files: dict[str, SkillSupportingFile] = Field(default_factory=dict)
    # Mini-ADR U-15: per-skill progressive disclosure flag. False = body
    # eager-loaded into system prompt (current behavior); True = body
    # lazy-loaded via ``skill_view`` tool.
    lazy_load: bool = False
    # Mini-ADR U-21: blake2b-32 hash of canonicalized content. Recomputed
    # at ``skill_view`` time; mismatch fires SKILL_DRIFT_DETECTED.
    # bytes(b"") on records written before the migration backfill;
    # consumers should compute via :func:`compute_content_hash` rather
    # than rely on whatever is in the row.
    content_hash: bytes = b""
    # Mini-ADR U-24: high-risk publish gate. True when tool_names ∩
    # HIGH_RISK_TOOLS ≠ ∅ or any supporting_files path starts with
    # "scripts/".
    high_risk: bool = False
    created_at: datetime


class SkillSupportingFile(BaseModel):
    """One entry in :attr:`SkillVersion.supporting_files`.

    Stored in Postgres as a JSONB object under the file's relative path.
    ``content`` is base64-encoded raw bytes so the JSONB blob is text-safe
    even for binary file types (PNG / SVG); the size cap (1 MB per file,
    5 MB per skill total) is enforced at the API layer.

    See Mini-ADR U-16 in ``docs/streams/STREAM-UPLIFT-DESIGN.md`` § 4.3.4.
    """

    model_config = ConfigDict(frozen=True)

    content: str  # base64 of raw bytes
    size: int = Field(ge=0)  # raw byte length (for cap checks + UI display)
    mime: str = ""


class Skill(BaseModel):
    """One row of ``skill`` — the named bundle.

    ``latest_version`` is the version number that bare ``name``
    references resolve to. When ``status == ACTIVE`` that version must
    be the highest-numbered active version; admin path mutations keep
    the invariant.

    ``description`` / ``category`` mirror the latest version's metadata
    so admin list responses don't need a join to render the listing.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    name: str
    status: SkillStatus
    latest_version: int = Field(ge=0)  # 0 only between create + first version insert
    description: str = ""
    category: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillRef(BaseModel):
    """Parsed form of an ``AgentSpec.skills`` element.

    A manifest's ``skills: list[str]`` may carry either a bare name
    (``"foo"``) or a pinned reference (``"foo@3"``). The validator on
    ``AgentSpecBody.skills`` parses each entry into this DTO so the
    orchestrator's skill loader gets a typed shape.

    ``version is None`` ⇒ bind ``skill.latest_version`` (skill must be
    in ``ACTIVE`` state). ``version is not None`` ⇒ pin to the exact
    ``skill_version.version`` (draft / active / archived all allowed —
    pinning is the reproducibility escape hatch).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=64)
    version: int | None = Field(default=None, ge=1)


#: Validator regex for ``AgentSpec.skills`` elements — see § 15.3.
#:
#: ``name`` allows lowercase letters / digits / dash / underscore, must
#: start with a letter, up to 64 chars; optional ``@N`` pins to a
#: specific ``skill_version.version`` (1-based positive integer).
SKILL_REF_PATTERN: str = r"^[a-z][a-z0-9_-]{0,63}(@[1-9][0-9]*)?$"


def parse_skill_ref(raw: str) -> SkillRef:
    """Parse a manifest ``skills`` entry into a :class:`SkillRef`.

    Mini-ADR J-23 (§ 15.3) — accepts ``"name"`` or ``"name@version"``;
    invalid input raises :class:`ValueError`. The orchestrator's skill
    loader uses this; the protocol-level ``AgentSpecBody.skills``
    validator delegates to it.
    """
    import re

    if not re.fullmatch(SKILL_REF_PATTERN, raw):
        msg = (
            f"skill ref {raw!r} is invalid; expected 'name' or 'name@version' "
            f"(name = [a-z][a-z0-9_-]{{0,63}}, version = positive int)"
        )
        raise ValueError(msg)
    if "@" in raw:
        name, version_str = raw.split("@", 1)
        return SkillRef(name=name, version=int(version_str))
    return SkillRef(name=raw, version=None)


# ─── Capability Uplift Sprint #3 helpers (Mini-ADR U-21 / U-24) ──────────


def is_high_risk_skill_version(
    *,
    tool_names: Iterable[str],
    supporting_file_paths: Iterable[str],
) -> bool:
    """Compute the ``high_risk`` flag for a skill version (Mini-ADR U-24).

    High-risk when **either**:

    1. ``tool_names`` intersects :data:`HIGH_RISK_TOOLS` (one of
       ``exec_python`` / ``exec_shell`` / ``http`` — tools that grant
       arbitrary code execution or unfiltered network egress); **or**
    2. Any supporting-file path starts with ``"scripts/"`` — convention
       for executable code intended to be picked up by ``exec_*`` tools.

    M0 reality: all skill mutations are admin-only so the publish gate
    is transparent. Will activate with M1-K J.7b-1 agent-self-authored
    skills, where an agent could declare ``exec_python`` and quietly
    drop a backdoor in ``scripts/diagnose.py``.
    """
    if HIGH_RISK_TOOLS & set(tool_names):
        return True
    return any(path.startswith("scripts/") for path in supporting_file_paths)


def canonicalize_skill_content(
    prompt_fragment: str,
    supporting_files: Mapping[str, object] | None = None,
) -> bytes:
    """Stable byte sequence for content hashing (Mini-ADR U-21).

    The hash is computed at write time + recomputed at every
    ``skill_view`` call;mismatch fires SKILL_DRIFT_DETECTED (almost
    certainly a SQL-injection or internal-actor signal). Hashing
    deterministically requires a canonical ordering of the
    ``supporting_files`` JSONB (Python dict insertion order is unstable
    when the row round-trips through Postgres).

    The serialization rule MUST match what migration 0042 backfill uses
    to seed existing M0 rows — otherwise every M0 skill_view would
    immediately fire a spurious P0 alert.
    """
    sorted_files = json.dumps(
        supporting_files or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return prompt_fragment.encode("utf-8") + b"\x00" + sorted_files.encode("utf-8")


def compute_content_hash(
    prompt_fragment: str,
    supporting_files: Mapping[str, object] | None = None,
) -> bytes:
    """``blake2b(_canonicalize(...), digest_size=32)`` — Mini-ADR U-21.

    32-byte digest is enough for collision resistance against accidental
    drift detection (we are not protecting against intentional collision
    crafting; the hash exists to detect tampering, not to be a MAC).
    """
    canonical = canonicalize_skill_content(prompt_fragment, supporting_files)
    return hashlib.blake2b(canonical, digest_size=32).digest()


def supporting_files_to_jsonable(
    supporting_files: Mapping[str, SkillSupportingFile],
) -> dict[str, dict[str, object]]:
    """Typed DTO → plain JSON shape for hashing / DB persist.

    Keys sorted so JSON serialization is deterministic across Python
    dict ordering; matches what :func:`canonicalize_skill_content`
    expects.
    """
    return {
        path: {
            "content": sf.content,
            "size": sf.size,
            "mime": sf.mime,
        }
        for path, sf in sorted(supporting_files.items())
    }
