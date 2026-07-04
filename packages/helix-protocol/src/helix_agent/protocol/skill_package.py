"""SKILL.md frontmatter parser + serializer — Capability Uplift Sprint #3.

Single source of truth for marshalling between the Claude Code standard
``SKILL.md`` (YAML frontmatter + Markdown body) format and helix's
typed ``SkillVersion`` DTO. Used by:

- ``_skill_zip.py`` — ZIP import / export
- ``skills.py`` API — single-file mutation (re-serialize SKILL.md on
  write)
- ``skill_view`` orchestrator tool — re-pack SKILL.md when the agent
  requests path ``"SKILL.md"``

Standard frontmatter (other Claude clients only read these):
- ``name`` (required, str)
- ``description`` (required, str)
- ``license`` (optional, str)

helix-specific extensions live under the ``helix:`` namespace key so
non-helix clients silently ignore them (Mini-ADR U-14):
- ``version`` (optional, int ≥ 1, default 1 — DB owns version numbering)
- ``category`` (optional, str)
- ``required_models`` (optional, list[str])
- ``tool_names`` (optional, list[str])
- ``authored_by`` (optional, ``"human" | "agent"``, default ``"human"``)
- ``lazy`` (optional, bool, default False)

Body = everything after the second ``---`` line. Becomes
``SkillVersion.prompt_fragment``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import yaml

from helix_agent.protocol.skill import (
    DEFAULT_SKILL_LAZY_LOAD,
    SkillAuthoredBy,
    SkillPackageLayoutError,
)

__all__ = [
    "FRONTMATTER_DELIMITER",
    "ParsedSkillMd",
    "parse_skill_md",
    "serialize_skill_md",
]

FRONTMATTER_DELIMITER: Final[str] = "---"


@dataclass(frozen=True)
class ParsedSkillMd:
    """Parsed SKILL.md split into the standard + helix fields + body.

    Returned by :func:`parse_skill_md` and consumed by the ZIP importer
    when promoting to a ``SkillVersion`` (which adds DB-side fields like
    ``id`` / ``tenant_id`` / ``created_at``).
    """

    # Standard frontmatter
    name: str
    description: str
    license: str | None
    # helix: namespace extension
    helix_version: int
    helix_category: str | None
    helix_required_models: tuple[str, ...]
    helix_tool_names: tuple[str, ...]
    helix_authored_by: SkillAuthoredBy
    helix_lazy: bool
    # Markdown body (= prompt_fragment)
    body: str


def parse_skill_md(text: str) -> ParsedSkillMd:
    """Parse a SKILL.md text into the typed :class:`ParsedSkillMd`.

    Raises :class:`SkillPackageLayoutError` on any structural problem
    (missing delimiters / invalid YAML / missing required fields / wrong
    type). The control-plane catches this and returns a generic 400 per
    Oracle defense (Mini-ADR U-18 / U-21).
    """
    if not text.startswith(FRONTMATTER_DELIMITER):
        msg = "SKILL.md must start with YAML frontmatter delimited by '---'"
        raise SkillPackageLayoutError(msg)

    # Split: text = "---" + frontmatter_yaml + "---\n" + body
    # Use splitlines + look for second "---" anchor.
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        msg = "SKILL.md must start with a '---' delimiter on its own line"
        raise SkillPackageLayoutError(msg)
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIMITER:
            closing_idx = i
            break
    if closing_idx < 0:
        msg = "SKILL.md is missing the closing '---' frontmatter delimiter"
        raise SkillPackageLayoutError(msg)

    frontmatter_text = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :]).lstrip("\n")

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        msg = f"SKILL.md frontmatter is not valid YAML: {exc}"
        raise SkillPackageLayoutError(msg) from exc

    if not isinstance(frontmatter, dict):
        msg = "SKILL.md frontmatter must be a YAML mapping at the top level"
        raise SkillPackageLayoutError(msg)

    # Standard fields
    name = _require_str(frontmatter, "name")
    description = _require_str(frontmatter, "description")
    license_val = frontmatter.get("license")
    if license_val is not None and not isinstance(license_val, str):
        msg = "SKILL.md 'license' must be a string when present"
        raise SkillPackageLayoutError(msg)

    # helix: namespace
    helix_block = frontmatter.get("helix", {}) or {}
    if not isinstance(helix_block, dict):
        msg = "SKILL.md 'helix:' field must be a YAML mapping"
        raise SkillPackageLayoutError(msg)

    # ``helix.version`` is a helix-internal field. Standard external SKILL.md
    # files (Anthropic/Vercel format, imported via GitHub / skills.sh) never
    # carry the ``helix:`` namespace, and the DB owns version numbering on
    # import regardless — so a missing version defaults to 1 rather than
    # rejecting the whole package. Type/range is still enforced when the field
    # is explicitly present (``bool`` is an ``int`` subclass, so guard it out).
    helix_version = helix_block.get("version", 1)
    if not isinstance(helix_version, int) or isinstance(helix_version, bool) or helix_version < 1:
        msg = "SKILL.md 'helix.version' must be an integer >= 1 when present"
        raise SkillPackageLayoutError(msg)

    helix_category = helix_block.get("category")
    if helix_category is not None and not isinstance(helix_category, str):
        msg = "SKILL.md 'helix.category' must be a string when present"
        raise SkillPackageLayoutError(msg)

    helix_required_models = _list_of_str(helix_block, "helix.required_models")
    helix_tool_names = _list_of_str(helix_block, "helix.tool_names")

    helix_authored_by_raw = helix_block.get("authored_by", "human")
    if helix_authored_by_raw not in ("human", "agent"):
        msg = (
            "SKILL.md 'helix.authored_by' must be 'human' or 'agent' "
            f"(got {helix_authored_by_raw!r})"
        )
        raise SkillPackageLayoutError(msg)

    # RT-ADR-11 — omitted ``lazy`` defaults to progressive disclosure so an
    # imported skill whose frontmatter never mentions helix-specific keys
    # (the common GitHub case) lands lazy, not eager.
    helix_lazy = helix_block.get("lazy", DEFAULT_SKILL_LAZY_LOAD)
    if not isinstance(helix_lazy, bool):
        msg = "SKILL.md 'helix.lazy' must be a boolean when present"
        raise SkillPackageLayoutError(msg)

    return ParsedSkillMd(
        name=name,
        description=description,
        license=license_val,
        helix_version=helix_version,
        helix_category=helix_category,
        helix_required_models=helix_required_models,
        helix_tool_names=helix_tool_names,
        helix_authored_by=helix_authored_by_raw,
        helix_lazy=helix_lazy,
        body=body,
    )


def serialize_skill_md(parsed: ParsedSkillMd) -> str:
    """Inverse of :func:`parse_skill_md` — produce canonical SKILL.md.

    Used by ZIP export + ``skill_view("X", "SKILL.md")``. Deterministic
    field ordering so consecutive exports of an unchanged skill diff
    cleanly in git.
    """
    helix_block: dict[str, object] = {"version": parsed.helix_version}
    if parsed.helix_category is not None:
        helix_block["category"] = parsed.helix_category
    if parsed.helix_required_models:
        helix_block["required_models"] = list(parsed.helix_required_models)
    if parsed.helix_tool_names:
        helix_block["tool_names"] = list(parsed.helix_tool_names)
    if parsed.helix_authored_by != "human":
        helix_block["authored_by"] = parsed.helix_authored_by
    # RT-ADR-11 — lazy is the default, so only the non-default (eager) case is
    # written; an omitted ``lazy`` round-trips back to lazy via the parser.
    if not parsed.helix_lazy:
        helix_block["lazy"] = False

    frontmatter: dict[str, object] = {
        "name": parsed.name,
        "description": parsed.description,
    }
    if parsed.license is not None:
        frontmatter["license"] = parsed.license
    frontmatter["helix"] = helix_block

    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"{FRONTMATTER_DELIMITER}\n{rendered}{FRONTMATTER_DELIMITER}\n\n{parsed.body}"


# ─── internal helpers ────────────────────────────────────────────────────


def _require_str(frontmatter: dict[str, object], key: str) -> str:
    value = frontmatter.get(key)
    if not isinstance(value, str) or not value:
        msg = f"SKILL.md frontmatter field {key!r} is required and must be a non-empty string"
        raise SkillPackageLayoutError(msg)
    return value


def _list_of_str(block: dict[str, object], field_path: str) -> tuple[str, ...]:
    key = field_path.split(".")[-1]
    value = block.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        msg = f"SKILL.md frontmatter field {field_path!r} must be a list of strings"
        raise SkillPackageLayoutError(msg)
    return tuple(value)
