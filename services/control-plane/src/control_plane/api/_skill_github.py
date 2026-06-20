"""GitHub skill import — resolve + fetch + scan-and-match (方案 A).

Pulls a skill out of a GitHub repository and hands a canonical ``.skill`` zip
blob to the existing platform import pipeline. Three pure-ish stages:

* :func:`resolve_github_source` — ``owner/repo`` shorthand / GitHub URL /
  ``skills.sh`` URL (sugar) → ``(owner, repo, ref, skill)``. Pure.
* :func:`download_github_archive` — fetch ``codeload.github.com`` zip. The ONLY
  network surface; host is fixed (SSRF面塌缩成单域名), size-capped, timed out.
* :func:`select_skill_zip` — scan the archive for ``SKILL.md`` folders, match
  ``skill`` against the folder basename (the ``npx skills --skill <name>``
  convention), and repack that subtree as a root-anchored ``.skill`` zip. Pure.

Design: docs/design/skill-github-import.md. GitHub-only (G1), platform-only (G2),
public repos only (G5).
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from helix_agent.common.url_validation import RemoteURLError, validate_remote_url

__all__ = [
    "GithubImportError",
    "ResolvedGithubSource",
    "download_github_archive",
    "resolve_github_source",
    "select_skill_zip",
]

_CODELOAD_HOST = "codeload.github.com"
# Charset guards — these segments are interpolated into the codeload URL, so they
# must not carry path-traversal / scheme-smuggling characters.
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SKILL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DEFAULT_REF = "HEAD"  # codeload resolves HEAD → the repo's default branch

# Download / extraction safety caps.
_MAX_ARCHIVE_BYTES = 20 * 1024 * 1024  # 20 MB compressed archive
_MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024  # 80 MB inflated (zip-bomb guard)
_MAX_ENTRIES = 5000
_FETCH_TIMEOUT_S = 15.0


class GithubImportError(Exception):
    """A GitHub skill import failed. ``status`` maps to the HTTP response."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class ResolvedGithubSource:
    owner: str
    repo: str
    ref: str
    skill: str | None


def resolve_github_source(
    source: str, *, skill: str | None = None, ref: str | None = None
) -> ResolvedGithubSource:
    """Normalize a source string + optional ``skill``/``ref`` selectors.

    Accepts ``owner/repo``, a ``github.com`` URL (optionally
    ``/tree/<ref>/...``), or a ``skills.sh/<owner>/<repo>/<name>`` URL (which
    also supplies ``skill``). Raises :class:`GithubImportError` (400) on any
    structurally invalid input — generic message, Oracle defense.
    """
    src = source.strip()
    if not src:
        raise GithubImportError("source is required")

    owner = repo = ""
    resolved_ref = ref
    resolved_skill = skill

    if "://" in src or src.startswith("www."):
        parsed = urlparse(src if "://" in src else f"https://{src}")
        host = (parsed.hostname or "").lower()
        parts = [p for p in parsed.path.split("/") if p]
        if host in {"github.com", "www.github.com"}:
            if len(parts) < 2:
                raise GithubImportError("invalid GitHub URL")
            owner, repo = parts[0], parts[1]
            # .../tree/<ref>/<subpath...> — take ref only; the skill is located
            # by scan-and-match, not by the URL subpath.
            if len(parts) >= 4 and parts[2] == "tree" and resolved_ref is None:
                resolved_ref = parts[3]
        elif host in {"skills.sh", "www.skills.sh"}:
            # skills.sh/<owner>/<repo>/<name> → sugar for owner/repo + skill.
            if len(parts) < 3:
                raise GithubImportError("invalid skills.sh URL")
            owner, repo = parts[0], parts[1]
            if resolved_skill is None:
                resolved_skill = parts[2]
        else:
            raise GithubImportError("only github.com / skills.sh sources are supported")
    else:
        # owner/repo shorthand.
        parts = [p for p in src.split("/") if p]
        if len(parts) != 2:
            raise GithubImportError("source must be 'owner/repo' or a github.com URL")
        owner, repo = parts[0], parts[1]

    repo = repo.removesuffix(".git")
    if not _OWNER_REPO_RE.match(owner) or not _OWNER_REPO_RE.match(repo):
        raise GithubImportError("invalid owner/repo")
    final_ref = resolved_ref if resolved_ref else _DEFAULT_REF
    if not _REF_RE.match(final_ref):
        raise GithubImportError("invalid ref")
    if resolved_skill is not None and not _SKILL_RE.match(resolved_skill):
        raise GithubImportError("invalid skill name")

    return ResolvedGithubSource(owner=owner, repo=repo, ref=final_ref, skill=resolved_skill)


async def download_github_archive(
    src: ResolvedGithubSource, *, client: httpx.AsyncClient | None = None
) -> bytes:
    """Download the repo zip from ``codeload.github.com`` (the only egress).

    Host is fixed and the URL is built from charset-validated segments, so the
    SSRF surface is a single domain; ``validate_remote_url`` is a private-IP
    backstop. Streamed with a hard size cap (zip-bomb / huge-repo guard).
    """
    url = f"https://{_CODELOAD_HOST}/{src.owner}/{src.repo}/zip/{src.ref}"
    try:
        validate_remote_url(url, allowed_schemes=("https",))
    except RemoteURLError as exc:  # pragma: no cover - defensive; host is fixed
        raise GithubImportError("invalid archive URL", status=400) from exc
    if urlparse(url).hostname != _CODELOAD_HOST:  # pragma: no cover - defensive
        raise GithubImportError("invalid archive URL", status=400)

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S, follow_redirects=True)
    try:
        chunks: list[bytes] = []
        total = 0
        async with http.stream("GET", url) as resp:
            if resp.status_code == 404:
                raise GithubImportError(
                    f"GitHub repo or ref not found: {src.owner}/{src.repo}@{src.ref}",
                    status=404,
                )
            if resp.status_code != 200:
                raise GithubImportError(
                    f"GitHub archive fetch failed (HTTP {resp.status_code})", status=502
                )
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_ARCHIVE_BYTES:
                    raise GithubImportError("GitHub archive exceeds size limit", status=413)
                chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise GithubImportError(f"GitHub archive fetch failed: {exc}", status=502) from exc
    finally:
        if owns_client:
            await http.aclose()


def _skill_dir_index(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map ``skill folder basename → folder prefix`` for every ``SKILL.md`` in
    the archive. GitHub archives nest everything under ``<repo>-<ref>/``."""
    index: dict[str, str] = {}
    for name in archive.namelist():
        if not name.endswith("/SKILL.md"):
            continue
        folder = name[: -len("/SKILL.md")] + "/"  # ".../skills/find-skills/"
        basename = folder.rstrip("/").rsplit("/", 1)[-1]
        if basename in index:
            raise GithubImportError(
                f"ambiguous skill name {basename!r}: multiple folders match", status=400
            )
        index[basename] = folder
    return index


def select_skill_zip(archive_bytes: bytes, *, skill: str | None) -> bytes:
    """Scan the GitHub archive, pick the requested skill folder, and repack it
    as a root-anchored ``.skill`` zip (``SKILL.md`` + supporting files at root).

    Selection mirrors ``npx skills --skill <name>``: match the folder basename.
    No ``skill`` + exactly one skill in the repo → that one. Ambiguity / miss →
    :class:`GithubImportError` listing the candidates.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise GithubImportError("downloaded archive is not a valid zip") from exc

    with archive:
        index = _skill_dir_index(archive)
        if not index:
            raise GithubImportError("no SKILL.md found in the repository", status=404)

        if skill is None:
            if len(index) > 1:
                raise GithubImportError(
                    "repository contains multiple skills; specify which one with "
                    f"'skill'. candidates: {', '.join(sorted(index))}",
                    status=400,
                )
            prefix = next(iter(index.values()))
        else:
            prefix = index.get(skill)
            if prefix is None:
                raise GithubImportError(
                    f"skill {skill!r} not found. candidates: {', '.join(sorted(index))}",
                    status=404,
                )

        return _repack_subtree(archive, prefix)


def _repack_subtree(archive: zipfile.ZipFile, prefix: str) -> bytes:
    """Repack everything under ``prefix`` into a fresh zip, stripping the prefix
    so ``SKILL.md`` sits at the root. zip-slip + size/count guards applied."""
    out = io.BytesIO()
    total = 0
    entries = 0
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as dest:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            rel = info.filename[len(prefix) :]
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                continue  # zip-slip / stray entry — skip
            entries += 1
            if entries > _MAX_ENTRIES:
                raise GithubImportError("skill has too many files", status=413)
            data = archive.read(info)
            total += len(data)
            if total > _MAX_UNCOMPRESSED_BYTES:
                raise GithubImportError("skill content exceeds size limit", status=413)
            dest.writestr(rel, data)
    return out.getvalue()
