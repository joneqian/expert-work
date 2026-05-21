"""J.7a Skill 静态启用 — DTOs.

Mini-ADR J-23 + 2026-05-21 修订 (STREAM-J-DESIGN § 15). M0 = prompt
片段 + tools 子集 (不含 code 字段)+ 版本化 + draft 闸门 + admin
CRUD API + ZIP import/export + ``name@version`` 版本固定.

These DTOs are the wire shape between control-plane (admin API),
orchestrator (skill loader at build time), and helix-persistence
(``SkillStore`` ABC).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Skill",
    "SkillAuthoredBy",
    "SkillRef",
    "SkillStatus",
    "SkillVersion",
    "parse_skill_ref",
]


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
    created_at: datetime


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
