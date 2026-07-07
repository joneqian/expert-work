"""Image references for multimodal input — Stream J.6.

A user uploads an image with a run; the bytes land in object storage and
the run message carries an opaque ``expert_work://image/...`` reference instead
of the bytes (base64 never enters the checkpointer). ``ImageRef`` is the
parsed form of that URI — the bridge between the protocol-level reference
string and the object-storage key.

See ``docs/streams/STREAM-J-DESIGN.md`` § 13.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

#: URI scheme prefix for an uploaded image reference.
IMAGE_REF_PREFIX: Final = "expert_work://image/"

#: A file extension is optional; when present it must be a short,
#: lowercase, dotted token. The upload endpoint derives it from the
#: content-type allowlist, never from an untrusted filename.
_EXT_RE = re.compile(r"\.[a-z0-9]{1,12}")

#: Canonical (dashed) UUID string length.
_UUID_LEN: Final = 36


@dataclass(frozen=True)
class ImageRef:
    """A parsed ``expert_work://image/{tenant_id}/{thread_id}/{image_id}{ext}`` URI.

    The reference is self-describing: ``tenant_id`` lets a tool reject a
    cross-tenant image without a store lookup, and ``storage_key`` derives
    the object-storage key deterministically (ADR-0004 key convention) so
    the upload endpoint and the resolver share one source of truth.
    """

    tenant_id: UUID
    thread_id: UUID
    image_id: UUID
    #: File extension including the leading dot (e.g. ``".png"``); empty
    #: when the upload had no recognised extension.
    ext: str = ""

    def to_uri(self) -> str:
        """Render the canonical ``expert_work://image/...`` reference string."""
        return f"{IMAGE_REF_PREFIX}{self.tenant_id}/{self.thread_id}/{self.image_id}{self.ext}"

    @property
    def storage_key(self) -> str:
        """Object-storage key for the image bytes (ADR-0004 § 2.3)."""
        return f"{self.tenant_id}/uploads/{self.thread_id}/{self.image_id}{self.ext}"


def parse_image_ref(uri: str) -> ImageRef:
    """Parse a ``expert_work://image/...`` reference; raise ``ValueError`` if malformed.

    This is a system boundary — the ``image_ref`` argument reaches it
    straight from an LLM tool call — so every component is validated.
    """
    if not uri.startswith(IMAGE_REF_PREFIX):
        msg = f"image ref must start with {IMAGE_REF_PREFIX!r}: {uri!r}"
        raise ValueError(msg)
    parts = uri[len(IMAGE_REF_PREFIX) :].split("/")
    if len(parts) != 3:
        msg = f"image ref must be {IMAGE_REF_PREFIX}<tenant>/<thread>/<image>: {uri!r}"
        raise ValueError(msg)
    tenant_raw, thread_raw, last = parts
    try:
        tenant_id = UUID(tenant_raw)
        thread_id = UUID(thread_raw)
    except ValueError as exc:
        msg = f"image ref tenant / thread segment is not a UUID: {uri!r}"
        raise ValueError(msg) from exc
    id_raw, ext = last[:_UUID_LEN], last[_UUID_LEN:]
    try:
        image_id = UUID(id_raw)
    except ValueError as exc:
        msg = f"image ref image-id segment is not a UUID: {uri!r}"
        raise ValueError(msg) from exc
    if ext and not _EXT_RE.fullmatch(ext):
        msg = f"image ref has a malformed extension {ext!r}: {uri!r}"
        raise ValueError(msg)
    return ImageRef(tenant_id=tenant_id, thread_id=thread_id, image_id=image_id, ext=ext)
