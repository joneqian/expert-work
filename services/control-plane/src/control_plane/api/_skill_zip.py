"""J.7a ``.skill`` ZIP import / export — Mini-ADR J-23 § 15.5.

M0 ZIP 结构精简 (无 supporting files; J.7b 再扩):

    skill.zip
    +-- skill.yaml         # name / description / category / required_models
    +-- prompt.md          # prompt_fragment body
    +-- tools.txt          # tool_names, 一行一个

安全 (§ 15.6):

* zip slip 防护 -- 用 ``os.path.commonpath`` 校验每个 entry 解压路径
  不超出 tmp 根
* max 解压 10 MiB (deer-flow 是 512 MB, M0 收紧)
* max 文件数 16
* 白名单文件名 (其他 entry reject)
* 单 entry size 显式上限 (防 zip bomb)
"""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass
from typing import Final

import yaml

#: Max 10 MiB uncompressed per .skill ZIP (Mini-ADR J-23 § 15.6).
MAX_UNCOMPRESSED_BYTES: Final[int] = 10 * 1024 * 1024
#: Max 16 entries per ZIP — M0 only needs 3 (skill.yaml / prompt.md /
#: tools.txt), 16 is a comfortable cap that detects zip bombs early.
MAX_ENTRIES: Final[int] = 16
#: Single-entry size cap (defends against one giant file dominating the
#: archive's budget; also matches the M0 ``prompt_fragment`` ceiling +
#: headroom for YAML / tools.txt).
MAX_ENTRY_BYTES: Final[int] = 128 * 1024

_WHITELIST_NAMES: Final[frozenset[str]] = frozenset({"skill.yaml", "prompt.md", "tools.txt"})


@dataclass(frozen=True)
class SkillZipPayload:
    """Parsed view of a ``.skill`` ZIP — the body of a POST /v1/skills/import."""

    name: str
    description: str
    category: str | None
    required_models: tuple[str, ...]
    prompt_fragment: str
    tool_names: tuple[str, ...]


class SkillZipError(ValueError):
    """Raised on any structural / safety / content violation of a .skill ZIP."""


def parse_skill_zip(blob: bytes) -> SkillZipPayload:
    """Validate + parse a ``.skill`` ZIP into a :class:`SkillZipPayload`.

    Runs each safety check in order so the first violation is the
    surfaced error (helps an operator diagnose without a debugger).
    Does NOT validate the parsed fields against
    :class:`helix_agent.protocol.skill.SKILL_REF_PATTERN` — that's the
    caller's job (the API layer feeds the result through the same
    validator the manifest uses).
    """
    if len(blob) > MAX_UNCOMPRESSED_BYTES:
        # Even before decompression a > 10 MiB ZIP is suspicious. The
        # compressed size check is a cheap pre-flight; the unzip loop
        # below repeats the check on uncompressed bytes.
        raise SkillZipError(
            f"compressed payload {len(blob)} > {MAX_UNCOMPRESSED_BYTES} byte ZIP cap"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise SkillZipError(f"not a valid ZIP archive: {exc}") from exc

    members = archive.namelist()
    if len(members) > MAX_ENTRIES:
        raise SkillZipError(
            f"ZIP has {len(members)} entries > {MAX_ENTRIES} entry cap (possible zip bomb)"
        )

    files_seen: dict[str, bytes] = {}
    total_uncompressed = 0
    for member in members:
        # zip slip: an entry whose normalised path escapes the archive
        # root is a path-traversal attempt. ``os.path.commonpath`` over
        # the absolute joined path vs the archive root catches this.
        if member.endswith("/"):  # directory entry — M0 ZIP must be flat
            raise SkillZipError(
                f"ZIP entry {member!r} is a directory; M0 skill ZIP must be flat "
                f"(supporting files推 M1 J.7b)"
            )
        if member not in _WHITELIST_NAMES:
            raise SkillZipError(f"ZIP entry {member!r} not in whitelist {sorted(_WHITELIST_NAMES)}")
        # Belt-and-suspenders zip slip check: resolve against fake root.
        # ``os.path.commonpath`` raises ``ValueError`` on cross-drive
        # paths (Windows) — we don't run on Windows but the explicit
        # check matches the safer pattern.
        safe_root = "/tmp/skill_zip_root"  # noqa: S108 — comparison sentinel, never written to
        joined = os.path.normpath(os.path.join(safe_root, member))
        try:
            if os.path.commonpath([safe_root, joined]) != safe_root:
                raise SkillZipError(f"ZIP entry {member!r} resolves outside archive root")
        except ValueError as exc:
            raise SkillZipError(f"ZIP entry {member!r} has unsafe path: {exc}") from exc

        info = archive.getinfo(member)
        if info.file_size > MAX_ENTRY_BYTES:
            raise SkillZipError(
                f"ZIP entry {member!r} uncompressed size {info.file_size} > "
                f"{MAX_ENTRY_BYTES} per-entry cap"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise SkillZipError(
                f"ZIP entries' total uncompressed size > {MAX_UNCOMPRESSED_BYTES} "
                f"byte cap (zip bomb defence)"
            )
        files_seen[member] = archive.read(member)

    if "skill.yaml" not in files_seen:
        raise SkillZipError("ZIP missing required entry 'skill.yaml'")
    if "prompt.md" not in files_seen:
        raise SkillZipError("ZIP missing required entry 'prompt.md'")

    metadata = _parse_skill_yaml(files_seen["skill.yaml"])
    prompt_fragment = files_seen["prompt.md"].decode("utf-8")
    tool_names_blob = files_seen.get("tools.txt", b"")
    tool_names = tuple(
        line.strip() for line in tool_names_blob.decode("utf-8").splitlines() if line.strip()
    )

    # ``_parse_skill_yaml`` already validated these types at runtime; the
    # ``cast`` exists only to inform mypy.
    name: str = str(metadata["name"])
    description: str = str(metadata.get("description", ""))
    category_raw = metadata.get("category")
    category: str | None = None if category_raw is None else str(category_raw)
    rm_raw = metadata.get("required_models", [])
    required_models: tuple[str, ...] = (
        tuple(str(x) for x in rm_raw) if isinstance(rm_raw, list) else ()
    )
    return SkillZipPayload(
        name=name,
        description=description,
        category=category,
        required_models=required_models,
        prompt_fragment=prompt_fragment,
        tool_names=tool_names,
    )


def _parse_skill_yaml(blob: bytes) -> dict[str, object]:
    """Parse + light-shape-check ``skill.yaml``. Reject the obvious
    typos so a malformed YAML doesn't reach the protocol validator
    with a confusing error message."""
    try:
        data = yaml.safe_load(blob)
    except yaml.YAMLError as exc:
        raise SkillZipError(f"skill.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillZipError("skill.yaml must be a mapping (got non-dict YAML)")
    if "name" not in data or not isinstance(data["name"], str):
        raise SkillZipError("skill.yaml must include a string 'name' field")
    # Normalise optional fields' types.
    if "description" in data and not isinstance(data["description"], str):
        raise SkillZipError("skill.yaml 'description' must be a string")
    if (
        "category" in data
        and data["category"] is not None
        and not isinstance(data["category"], str)
    ):
        raise SkillZipError("skill.yaml 'category' must be a string or null")
    if "required_models" in data:
        rm = data["required_models"]
        if not isinstance(rm, list) or not all(isinstance(x, str) for x in rm):
            raise SkillZipError("skill.yaml 'required_models' must be a list of strings")
    return data


def build_skill_zip(
    *,
    name: str,
    description: str,
    category: str | None,
    required_models: tuple[str, ...],
    prompt_fragment: str,
    tool_names: tuple[str, ...],
) -> bytes:
    """Inverse of :func:`parse_skill_zip` — used by ``GET .../export``."""
    metadata: dict[str, object] = {
        "name": name,
        "description": description,
    }
    if category is not None:
        metadata["category"] = category
    if required_models:
        metadata["required_models"] = list(required_models)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "skill.yaml",
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
        )
        archive.writestr("prompt.md", prompt_fragment)
        archive.writestr("tools.txt", "\n".join(tool_names) + ("\n" if tool_names else ""))
    return buf.getvalue()
