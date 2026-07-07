"""Dataset acquisition with sha256 pinning — Stream CM-N5 (Mini-ADR CM-K3).

Neither benchmark is vendored into the repo: LoCoMo is CC BY-NC 4.0
(re-distribution from a commercial repo is a license exposure) and the
LongMemEval_S file is 277MB. Instead this module downloads the pinned
upstream files into a gitignored cache directory and verifies their
sha256 before first use. Pins were taken 2026-06-10 from the upstream
sources (LoCoMo verified by local download; LongMemEval oracle verified
locally against the HuggingFace LFS oid; the S/M files pinned from the
HF API oid).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

#: Cache directory override — points the harness at an existing mirror.
_CACHE_ENV = "EXPERT_WORK_LONGMEM_CACHE"

_DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "datasets" / ".longmem_cache"

#: Streaming chunk size for downloads (the S file is 277MB).
_CHUNK = 1 << 20


@dataclass(frozen=True)
class DatasetSpec:
    """One pinned upstream file."""

    key: str
    url: str
    sha256: str
    size_bytes: int
    filename: str


DATASETS: dict[str, DatasetSpec] = {
    "locomo10": DatasetSpec(
        key="locomo10",
        url="https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json",
        sha256="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4",
        size_bytes=2_805_274,
        filename="locomo10.json",
    ),
    "longmemeval_oracle": DatasetSpec(
        key="longmemeval_oracle",
        url=(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
            "resolve/main/longmemeval_oracle.json"
        ),
        sha256="821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
        size_bytes=15_388_478,
        filename="longmemeval_oracle.json",
    ),
    "longmemeval_s": DatasetSpec(
        key="longmemeval_s",
        url=(
            "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
            "resolve/main/longmemeval_s_cleaned.json"
        ),
        sha256="d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
        size_bytes=277_383_467,
        filename="longmemeval_s_cleaned.json",
    ),
}


class DatasetIntegrityError(RuntimeError):
    """A cached or downloaded file does not match its pinned sha256."""


def cache_dir() -> Path:
    """The dataset cache directory (gitignored; env-overridable)."""
    override = os.environ.get(_CACHE_ENV)
    return Path(override) if override else _DEFAULT_CACHE


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, spec: DatasetSpec) -> None:
    """Raise :class:`DatasetIntegrityError` unless ``path`` matches ``spec``."""
    actual = file_sha256(path)
    if actual != spec.sha256:
        raise DatasetIntegrityError(
            f"{spec.key}: sha256 mismatch at {path} — expected {spec.sha256}, got {actual}. "
            "Delete the file to re-download, or update the pin if upstream legitimately changed."
        )


def ensure_dataset(key: str, *, client: httpx.Client | None = None) -> Path:
    """Return the verified local path for ``key``, downloading if absent.

    Downloads stream to a ``.part`` sibling and rename into place only
    after the sha256 check passes, so an interrupted download never
    leaves a plausible-looking corrupt file in the cache.
    """
    spec = DATASETS[key]
    target = cache_dir() / spec.filename
    if target.exists():
        verify(target, spec)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    own_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True)
    try:
        with http.stream("GET", spec.url) as response:
            response.raise_for_status()
            with part.open("wb") as fh:
                for chunk in response.iter_bytes(_CHUNK):
                    fh.write(chunk)
    finally:
        if own_client:
            http.close()
    try:
        verify(part, spec)
    except DatasetIntegrityError:
        part.unlink(missing_ok=True)
        raise
    part.replace(target)
    return target
