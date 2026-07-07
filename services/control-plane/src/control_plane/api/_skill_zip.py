"""``.skill`` ZIP import / export — Capability Uplift Sprint #3 (Mini-ADR U-14 … U-24).

Canonical layout (Mini-ADR U-14):

    skill.zip
    ├── SKILL.md                       # YAML frontmatter + Markdown body
    ├── reference/...                  # arbitrary subdirectories
    ├── templates/...
    └── scripts/diagnose.py

Legacy layout (Mini-ADR U-19 backward compat, read-only):

    skill.zip
    ├── skill.yaml
    ├── prompt.md
    └── tools.txt

Safety layers stacked at ZIP boundary:

* **U-18** — path / character / extension / size validation. Any violation
  rejects the **entire** ZIP. Errors raised to the API caller are
  intentionally generic (Oracle defense); full reason goes to audit.
* **U-21** — write-time threat scan of SKILL.md body + every text
  supporting file via :func:`scan_for_threats` (scope=``"strict"``). Any
  finding rejects the entire ZIP.
* **U-22 / U-23** — obfuscation defense (NFKC / base64 / whitespace) +
  Chinese pattern set live inside :func:`scan_for_threats`; transparent
  to this module.
* **U-24** — ``high_risk`` flag derived from declared tool_names +
  ``scripts/`` paths via :func:`is_high_risk_skill_version`.

The reject-reason taxonomy (Sprint #3 § 4.7) is enforced by the
:class:`_ZipRejectReason` Literal — values are an allow-list so the
``record_skill_zip_reject`` metric never carries user paths.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from typing import Final, Literal, NoReturn

from expert_work.common.threat_patterns import ThreatFinding, scan_for_threats
from expert_work.common.uplift_metrics import (
    record_skill_blocked,
    record_skill_zip_reject,
    record_threat_pattern_hits,
)
from expert_work.protocol.skill import (
    DEFAULT_SKILL_LAZY_LOAD,
    SkillAuthoredBy,
    SkillPackageLayoutError,
    SkillSupportingFile,
    compute_content_hash,
    is_high_risk_skill_version,
    supporting_files_to_jsonable,
)
from expert_work.protocol.skill_package import (
    ParsedSkillMd,
    parse_skill_md,
    serialize_skill_md,
)

logger = logging.getLogger("expert_work.control_plane.skill_zip")


# ─── Limits (Mini-ADR U-16 / U-18) ───────────────────────────────────────
#
# Calibrated against the canonical real-world skills the GitHub import feature
# targets — Anthropic's own ``anthropics/skills`` repo. Their ``pptx`` skill
# bundles 59 files nested 5 dirs deep (``scripts/office/schemas/.../*.xsd``)
# totalling ~1.1 MiB, with a tree of ``.xsd`` XML schemas. The original limits
# (depth 3, 64 entries, no ``.xsd``) were tuned for tiny hand-authored expert_work
# skills and rejected Anthropic's reference skill on three axes at once. These
# values give real skills headroom while the per-file / total-size caps remain
# the actual resource bound.

#: Per-file uncompressed size cap.
MAX_FILE_BYTES: Final[int] = 1 * 1024 * 1024  # 1 MiB
#: Total uncompressed size across all entries.
MAX_TOTAL_BYTES: Final[int] = 5 * 1024 * 1024  # 5 MiB
#: Max number of entries (excluding pure-directory entries). Real skills bundle
#: script trees + schema sets (anthropics/skills pptx = 59), so 64 was too tight.
MAX_ENTRIES: Final[int] = 256
#: Max characters in a relative path.
MAX_PATH_LENGTH: Final[int] = 256
#: Max subdirectory nesting under the root. anthropics/skills pptx nests 5 deep
#: (``scripts/office/schemas/ecma/fouth-edition/opc-*.xsd``); 6 adds headroom.
MAX_PATH_DEPTH: Final[int] = 6

#: Allow-listed file extensions (case-sensitive on suffix, lowercase compare).
#: ``.xsd`` / ``.xml`` / ``.csv`` / ``.tsv`` / ``.ini`` / ``.cfg`` / ``.rst``
#: cover real skill content (OOXML schemas, data tables, config, docs). All are
#: text, so they also join ``TEXT_EXTENSIONS`` below and get threat-scanned —
#: no new binary attack surface.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".sh",
        ".toml",
        ".html",
        ".css",
        ".xsd",
        ".xml",
        ".csv",
        ".tsv",
        ".ini",
        ".cfg",
        ".rst",
        ".png",
        ".jpg",
        ".svg",
    }
)

#: Extensions treated as text for the U-21 strict scan. Binary types
#: (.png / .jpg / .svg) are skipped — they cannot encode an LLM prompt.
TEXT_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".sh",
        ".toml",
        ".html",
        ".css",
        ".xsd",
        ".xml",
        ".csv",
        ".tsv",
        ".ini",
        ".cfg",
        ".rst",
    }
)

#: Per-segment filename regex (Mini-ADR U-18 table row "字符").
_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9_.\-]+$")

#: Symlink mode bits in ZIP ``external_attr`` (upper 16 bits = POSIX mode).
_SYMLINK_MODE: Final[int] = 0o120000
_FILE_TYPE_MASK: Final[int] = 0o170000

#: Legacy entry names (Mini-ADR U-19).
_LEGACY_SKILL_YAML: Final[str] = "skill.yaml"
_LEGACY_PROMPT_MD: Final[str] = "prompt.md"
_LEGACY_TOOLS_TXT: Final[str] = "tools.txt"
_LEGACY_ENTRIES: Final[frozenset[str]] = frozenset(
    {
        _LEGACY_SKILL_YAML,
        _LEGACY_PROMPT_MD,
        _LEGACY_TOOLS_TXT,
    }
)

_NEW_SKILL_MD: Final[str] = "SKILL.md"


# ─── Reject reason taxonomy (Sprint #3 § 4.7) ────────────────────────────

_ZipRejectReason = Literal[
    "missing_skill_md",
    "invalid_frontmatter",
    "path_traversal",
    "symlink",
    "absolute_path",
    "invalid_chars",
    "depth_exceeded",
    "extension_not_allowed",
    "file_too_large",
    "total_too_large",
    "too_many_entries",
    "prompt_injection",
    "legacy_format",
    "bad_zip",
    "binary_in_text_file",
]


# ─── Public types ────────────────────────────────────────────────────────


class SkillPackageError(SkillPackageLayoutError):
    """Internal-detail-bearing subclass of :class:`SkillPackageLayoutError`.

    The API layer catches :class:`SkillPackageLayoutError` and returns a
    generic 400 to the user (Oracle defense). The ``reason`` /
    ``findings`` / ``internal_message`` attributes are populated for
    audit consumption and never returned to the caller.
    """

    def __init__(
        self,
        public_message: str = "invalid skill package",
        *,
        reason: _ZipRejectReason,
        internal_message: str | None = None,
        findings: tuple[ThreatFinding, ...] = (),
    ) -> None:
        super().__init__(public_message)
        self.reason: _ZipRejectReason = reason
        self.internal_message: str = internal_message or public_message
        self.findings: tuple[ThreatFinding, ...] = findings


#: Backward-compat alias: callers that used to catch ``SkillZipError``
#: keep working — every reject still bubbles a ``SkillPackageLayoutError``
#: (which ``SkillPackageError`` is a subclass of).
SkillZipError = SkillPackageLayoutError


@dataclass(frozen=True)
class SkillZipPayload:
    """Parsed view of a ``.skill`` ZIP — body of ``POST /v1/skills/import``.

    Backward compatible with the J.7a M0 payload (caller still reads
    ``name`` / ``prompt_fragment`` / ``tool_names`` etc), extended with
    Sprint #3 Mini-ADR U-16 / U-21 / U-24 fields. The supplemental fields
    have sensible defaults so existing callers that don't yet thread them
    through the store keep working.
    """

    name: str
    description: str
    category: str | None
    required_models: tuple[str, ...]
    prompt_fragment: str
    tool_names: tuple[str, ...]
    # Sprint #3 additions
    license: str | None = None
    authored_by: SkillAuthoredBy = "human"
    lazy_load: bool = DEFAULT_SKILL_LAZY_LOAD
    supporting_files: dict[str, SkillSupportingFile] = field(default_factory=dict)
    content_hash: bytes = b""
    high_risk: bool = False
    layout: Literal["new", "legacy"] = "new"


# ─── Public API ──────────────────────────────────────────────────────────


def parse_skill_zip(blob: bytes) -> SkillZipPayload:
    """Validate + parse a ``.skill`` ZIP into a :class:`SkillZipPayload`.

    Raises :class:`SkillPackageLayoutError` on any structural / safety /
    content violation. The API layer must NOT echo the message back to
    the caller (Oracle defense, Mini-ADR U-18 / U-21) — it should catch
    :class:`SkillPackageLayoutError`, log the full ``SkillPackageError``
    detail to audit (if available), and return a fixed 400.
    """
    # Pre-flight: opening the ZIP itself can fail (truncated upload,
    # not-a-zip bytes). Both paths bubble the same generic reject.
    archive = _open_archive(blob)
    try:
        entries = _collect_entries(archive)

        # Layout detection (Mini-ADR U-19).
        if _NEW_SKILL_MD in entries.names:
            return _parse_new_format(entries)
        if _LEGACY_SKILL_YAML in entries.names and _LEGACY_PROMPT_MD in entries.names:
            return _parse_legacy_format(entries)
        _reject(
            reason="missing_skill_md",
            internal=(
                "ZIP missing 'SKILL.md' (new format) and missing skill.yaml+prompt.md pair (legacy)"
            ),
        )
    finally:
        archive.close()


def build_skill_zip(
    *,
    name: str,
    description: str,
    category: str | None,
    required_models: tuple[str, ...],
    prompt_fragment: str,
    tool_names: tuple[str, ...],
    license: str | None = None,
    authored_by: SkillAuthoredBy = "human",
    lazy: bool = False,
    version: int = 1,
    supporting_files: dict[str, SkillSupportingFile] | None = None,
) -> bytes:
    """Inverse of :func:`parse_skill_zip` — always emits new SKILL.md format.

    The legacy format (skill.yaml + prompt.md + tools.txt) is **read
    only** (Mini-ADR U-19); export always upgrades to canonical.

    Claude Code's SKILL.md standard requires ``description`` to be a
    non-empty string. Existing expert_work skill rows may legitimately carry an
    empty description (the M0 API made it optional); in that case we
    write a synthetic placeholder so the exported ZIP round-trips
    through :func:`parse_skill_zip`.
    """
    effective_description = description or f"{name} (no description)"
    parsed = ParsedSkillMd(
        name=name,
        description=effective_description,
        license=license,
        expert_work_version=version,
        expert_work_category=category,
        expert_work_required_models=required_models,
        expert_work_tool_names=tool_names,
        expert_work_authored_by=authored_by,
        expert_work_lazy=lazy,
        body=prompt_fragment,
    )
    skill_md = serialize_skill_md(parsed)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_NEW_SKILL_MD, skill_md)
        for path, sf in sorted((supporting_files or {}).items()):
            try:
                raw = base64.b64decode(sf.content, validate=True)
            except (ValueError, TypeError) as exc:
                msg = f"supporting file {path!r} content is not valid base64: {exc}"
                raise SkillPackageLayoutError(msg) from exc
            archive.writestr(path, raw)
    return buf.getvalue()


# ─── Internals ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _ArchiveEntries:
    """One pass over ``ZipFile.infolist()`` — names + per-name bytes."""

    names: frozenset[str]
    data: dict[str, bytes]


def _open_archive(blob: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        _reject(reason="bad_zip", internal=f"not a valid ZIP: {exc}")


def _collect_entries(archive: zipfile.ZipFile) -> _ArchiveEntries:
    """Pull every entry's bytes + run the per-entry path/size guards.

    Centralised so the new-format and legacy-format parsers both benefit
    from the same defense set without re-implementing.
    """
    infolist = archive.infolist()
    file_infos = [info for info in infolist if not info.is_dir()]
    if len(file_infos) == 0:
        _reject(reason="missing_skill_md", internal="ZIP is empty")
    if len(file_infos) > MAX_ENTRIES:
        _reject(
            reason="too_many_entries",
            internal=f"ZIP has {len(file_infos)} entries > {MAX_ENTRIES} cap",
        )

    total = 0
    data: dict[str, bytes] = {}
    for info in file_infos:
        name = info.filename
        _validate_path(name)
        _validate_not_symlink(info)
        if info.file_size > MAX_FILE_BYTES:
            _reject(
                reason="file_too_large",
                internal=f"entry {name!r} size {info.file_size} > {MAX_FILE_BYTES}",
            )
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            _reject(
                reason="total_too_large",
                internal=f"total uncompressed > {MAX_TOTAL_BYTES} byte cap",
            )
        # Read once; cap CPU/RAM by the per-entry size guard above.
        data[name] = archive.read(info)

    return _ArchiveEntries(names=frozenset(data.keys()), data=data)


def _validate_path(name: str) -> None:
    """Apply the Mini-ADR U-18 path table.

    NOTE: order matters — absolute-path / traversal checks come first so
    a single attack vector trips the most specific reason code.
    """
    if len(name) > MAX_PATH_LENGTH:
        _reject(
            reason="invalid_chars",
            internal=f"path length {len(name)} > {MAX_PATH_LENGTH}",
        )
    if not name:
        _reject(reason="invalid_chars", internal="empty path")
    # Reject any backslashes (Windows-style paths) before treating as POSIX.
    if "\\" in name:
        _reject(reason="invalid_chars", internal=f"path {name!r} contains backslash")
    # Absolute paths: POSIX leading "/" or Windows "X:" prefix.
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        _reject(reason="absolute_path", internal=f"absolute path: {name!r}")
    segments = name.split("/")
    if any(seg == ".." for seg in segments):
        _reject(reason="path_traversal", internal=f"'..' segment in {name!r}")
    if any(seg in {".", ""} for seg in segments):
        _reject(reason="invalid_chars", internal=f"empty/dot segment in {name!r}")
    # Nesting: SKILL.md at root = 0 dirs; reference/foo.md = 1 dir;
    # cap at MAX_PATH_DEPTH directories above the file.
    if len(segments) - 1 > MAX_PATH_DEPTH:
        _reject(
            reason="depth_exceeded",
            internal=f"path {name!r} nests {len(segments) - 1} > {MAX_PATH_DEPTH} dirs",
        )
    for seg in segments:
        if not _SEGMENT_RE.match(seg):
            _reject(
                reason="invalid_chars",
                internal=f"segment {seg!r} in {name!r} fails ^[a-zA-Z0-9_.\\-]+$",
            )
    # Extension allowlist — applies to every file (SKILL.md is .md so it
    # passes naturally; the must-exist-at-root check is handled elsewhere).
    ext = _extension(name)
    if ext not in ALLOWED_EXTENSIONS:
        _reject(
            reason="extension_not_allowed",
            internal=f"extension {ext!r} of {name!r} not in allowlist",
        )


def _validate_not_symlink(info: zipfile.ZipInfo) -> None:
    """Reject ZIP entries flagged as symlinks (POSIX file-type bits).

    ZIP's ``external_attr`` upper 16 bits encode the POSIX mode for
    Unix-created archives. The file-type field == ``0o120000`` indicates
    a symbolic link, which would let an attacker stage an arbitrary-path
    pointer that the OS then resolves when read.
    """
    mode = info.external_attr >> 16
    if mode & _FILE_TYPE_MASK == _SYMLINK_MODE:
        _reject(reason="symlink", internal=f"symlink entry: {info.filename!r}")


def _extension(name: str) -> str:
    idx = name.rfind(".")
    if idx < 0:
        return ""
    return name[idx:].lower()


def _parse_new_format(entries: _ArchiveEntries) -> SkillZipPayload:
    """Parse a SKILL.md-rooted ZIP."""
    skill_md_bytes = entries.data[_NEW_SKILL_MD]
    try:
        # utf-8-sig: tolerate a leading BOM so a BOM-prefixed SKILL.md doesn't
        # fail the "must start with '---'" frontmatter check with a misleading
        # error (some editors emit UTF-8-with-BOM).
        skill_md_text = skill_md_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _reject(
            reason="invalid_frontmatter",
            internal=f"SKILL.md is not valid UTF-8: {exc}",
        )
    try:
        parsed = parse_skill_md(skill_md_text)
    except SkillPackageLayoutError as exc:
        _reject(reason="invalid_frontmatter", internal=str(exc))

    # Collect supporting files (everything except SKILL.md at root).
    supporting_raw: dict[str, bytes] = {
        path: raw for path, raw in entries.data.items() if path != _NEW_SKILL_MD
    }
    _scan_for_threats(parsed.body, supporting_raw)

    supporting_files = _build_supporting_files(supporting_raw)
    jsonable = supporting_files_to_jsonable(supporting_files)
    content_hash = compute_content_hash(parsed.body, jsonable)
    high_risk = is_high_risk_skill_version(
        tool_names=parsed.expert_work_tool_names,
        supporting_file_paths=supporting_files.keys(),
    )

    return SkillZipPayload(
        name=parsed.name,
        description=parsed.description,
        category=parsed.expert_work_category,
        required_models=parsed.expert_work_required_models,
        prompt_fragment=parsed.body,
        tool_names=parsed.expert_work_tool_names,
        license=parsed.license,
        authored_by=parsed.expert_work_authored_by,
        lazy_load=parsed.expert_work_lazy,
        supporting_files=supporting_files,
        content_hash=content_hash,
        high_risk=high_risk,
        layout="new",
    )


def _parse_legacy_format(entries: _ArchiveEntries) -> SkillZipPayload:
    """Parse the legacy 3-file format and emit a deprecation warning.

    Mini-ADR U-19 — read only. New supporting files in the legacy layout
    are rejected (any extra entry beyond the 3-name allowlist is a
    structural error).
    """
    stray = entries.names - _LEGACY_ENTRIES
    if stray:
        # Don't surface specific paths to the user; record the count for
        # the operator-facing internal message.
        _reject(
            reason="missing_skill_md",
            internal=(
                f"legacy ZIP has {len(stray)} unexpected entries; "
                "supporting files require the SKILL.md format"
            ),
        )

    # Static counter bump for the legacy-format warning path. (Not a
    # reject — caller still proceeds.)
    record_skill_zip_reject(reason="legacy_format")
    logger.warning(
        "skill_zip.legacy_format_detected; please re-export to SKILL.md format",
    )

    # Parse the three files.
    import yaml  # local — yaml is already a project dep; keeps import surface small.

    try:
        raw_yaml = entries.data[_LEGACY_SKILL_YAML].decode("utf-8")
        metadata = yaml.safe_load(raw_yaml)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        _reject(
            reason="invalid_frontmatter",
            internal=f"legacy skill.yaml is not valid: {exc}",
        )
    if not isinstance(metadata, dict):
        _reject(
            reason="invalid_frontmatter",
            internal="legacy skill.yaml must be a YAML mapping",
        )
    name_raw = metadata.get("name")
    if not isinstance(name_raw, str) or not name_raw:
        _reject(
            reason="invalid_frontmatter",
            internal="legacy skill.yaml missing 'name' string field",
        )
    description_raw = metadata.get("description", "")
    if not isinstance(description_raw, str):
        _reject(
            reason="invalid_frontmatter",
            internal="legacy skill.yaml 'description' must be a string",
        )
    category_raw = metadata.get("category")
    if category_raw is not None and not isinstance(category_raw, str):
        _reject(
            reason="invalid_frontmatter",
            internal="legacy skill.yaml 'category' must be a string or null",
        )
    rm_raw = metadata.get("required_models", [])
    if not isinstance(rm_raw, list) or not all(isinstance(x, str) for x in rm_raw):
        _reject(
            reason="invalid_frontmatter",
            internal="legacy skill.yaml 'required_models' must be list of strings",
        )

    try:
        prompt_fragment = entries.data[_LEGACY_PROMPT_MD].decode("utf-8")
    except UnicodeDecodeError as exc:
        _reject(reason="invalid_frontmatter", internal=f"legacy prompt.md UTF-8: {exc}")

    tool_names_blob = entries.data.get(_LEGACY_TOOLS_TXT, b"")
    try:
        tool_names_text = tool_names_blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        _reject(reason="invalid_frontmatter", internal=f"legacy tools.txt UTF-8: {exc}")
    tool_names = tuple(line.strip() for line in tool_names_text.splitlines() if line.strip())

    # U-21 scan (legacy still subject to write-time defense).
    _scan_for_threats(prompt_fragment, {})

    content_hash = compute_content_hash(prompt_fragment, {})
    high_risk = is_high_risk_skill_version(
        tool_names=tool_names,
        supporting_file_paths=(),
    )

    return SkillZipPayload(
        name=name_raw,
        description=description_raw,
        category=category_raw,
        required_models=tuple(rm_raw),
        prompt_fragment=prompt_fragment,
        tool_names=tool_names,
        license=None,
        authored_by="human",
        # Legacy layout carries no frontmatter, hence no ``lazy`` hint —
        # default to progressive disclosure like every other new skill (RT-ADR-11).
        lazy_load=DEFAULT_SKILL_LAZY_LOAD,
        supporting_files={},
        content_hash=content_hash,
        high_risk=high_risk,
        layout="legacy",
    )


def _build_supporting_files(
    raw: dict[str, bytes],
) -> dict[str, SkillSupportingFile]:
    """Wrap raw supporting-file bytes into the typed DTO."""
    return {
        path: SkillSupportingFile(
            content=base64.b64encode(content).decode("ascii"),
            size=len(content),
            mime=_guess_mime(path),
        )
        for path, content in sorted(raw.items())
    }


def _guess_mime(path: str) -> str:
    """Map a path extension to a best-guess MIME type.

    Keeps the set small + offline (no ``mimetypes`` round-trip — its
    behavior varies by OS / Python build).
    """
    ext = _extension(path)
    return {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".json": "application/json",
        ".py": "text/x-python",
        ".js": "application/javascript",
        ".ts": "application/typescript",
        ".sh": "application/x-sh",
        ".toml": "application/toml",
        ".html": "text/html",
        ".css": "text/css",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")


def _scan_for_threats(
    skill_md_body: str,
    supporting_raw: dict[str, bytes],
) -> None:
    """Run the U-21 strict-scope threat scan on body + text files.

    Aggregates findings across all scanned content; if non-empty, records
    metrics + raises :class:`SkillPackageError` with ``reason=prompt_injection``
    and the full finding list attached. Caller (API layer) writes the
    audit row with the full detail; the user-facing message stays generic.
    """
    findings: list[ThreatFinding] = list(scan_for_threats(skill_md_body, scope="strict"))

    for path, raw in sorted(supporting_raw.items()):
        ext = _extension(path)
        if ext not in TEXT_EXTENSIONS:
            continue
        try:
            # ``utf-8-sig`` strips a single LEADING BOM (U+FEFF) — an encoding
            # marker, not content. UTF-8-with-BOM is standard for ECMA/MS XML
            # (e.g. the OOXML ``.xsd`` schemas Anthropic's pptx skill bundles),
            # and a leading BOM would otherwise trip the invisible-unicode
            # injection rule as a false positive. A U+FEFF *inside* the text is
            # left intact, so genuine zero-width obfuscation is still caught.
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # A "text" extension that doesn't decode is suspicious — could
            # be smuggling binary payload under a .md extension to dodge
            # the scanner. Reject outright.
            _reject(
                reason="binary_in_text_file",
                internal=f"supporting file {path!r} has text extension but binary content",
            )
        findings.extend(scan_for_threats(text, scope="strict"))

    if not findings:
        return

    # Don't dedupe across files — caller audit row gets the raw list. The
    # uplift_metrics dedupes by (pattern_id, scope, variant) labels.
    record_threat_pattern_hits(findings, scope="strict")
    record_skill_blocked(phase="zip_import")
    record_skill_zip_reject(reason="prompt_injection")
    raise SkillPackageError(
        reason="prompt_injection",
        internal_message=f"threat scan matched {len(findings)} finding(s)",
        findings=tuple(findings),
    )


def _reject(*, reason: _ZipRejectReason, internal: str) -> NoReturn:
    """Record metric + raise the generic-message ``SkillPackageError``.

    Centralised so every reject path emits exactly one
    ``record_skill_zip_reject`` count + carries the structured reason on
    the exception for audit consumption.
    """
    record_skill_zip_reject(reason=reason)
    raise SkillPackageError(reason=reason, internal_message=internal)
