"""Image content blocks and resolution for multimodal input — Stream J.6.

A run message carries an uploaded image as an ``image_ref`` content
block — ``{"type": "image_ref", "ref": "expert_work://image/..."}`` — instead
of inline base64, which would bloat every checkpoint snapshot. The
provider adapters resolve the reference to bytes through an
:class:`ImageResolver` only at the moment they build the wire payload.

This module is a leaf — block helpers + the resolver interface, no
orchestrator-internal imports — so the ``llm`` adapter layer can import
it without an ``llm ↔ tools`` cycle.

See ``docs/streams/STREAM-J-DESIGN.md`` § 13.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from expert_work.protocol.multimodal import parse_image_ref
from expert_work.runtime.storage.base import ObjectStore

#: ``content`` block discriminator for an uploaded-image reference.
IMAGE_REF_BLOCK_TYPE: Final = "image_ref"

#: Image media type per file extension — the J.6 supported set. The
#: upload endpoint sets the extension from the validated content type,
#: so every reference resolves to exactly one of these.
_MEDIA_TYPE_BY_EXT: Final[dict[str, str]] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def image_ref_block(uri: str) -> dict[str, str]:
    """Build the ``image_ref`` content block for a ``expert_work://image/...`` URI."""
    return {"type": IMAGE_REF_BLOCK_TYPE, "ref": uri}


def split_human_content(content: str | Sequence[object]) -> tuple[str, list[str]]:
    """Split a ``HumanMessage.content`` into ``(text, image-ref URIs)``.

    ``content`` is either a plain string or a LangChain block list. Text
    blocks are concatenated; ``image_ref`` blocks contribute their URI.
    """
    if isinstance(content, str):
        return content, []
    text_parts: list[str] = []
    image_refs: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, Mapping):
            if block.get("type") == IMAGE_REF_BLOCK_TYPE:
                ref = block.get("ref")
                if isinstance(ref, str):
                    image_refs.append(ref)
            else:
                text = block.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
    return "".join(text_parts), image_refs


@dataclass(frozen=True)
class ResolvedImage:
    """Image bytes + media type, resolved from an ``image_ref``."""

    media_type: str
    data: bytes = field(repr=False)

    @property
    def base64_data(self) -> str:
        """Base64-encoded image payload (ASCII)."""
        return base64.b64encode(self.data).decode("ascii")

    @property
    def data_uri(self) -> str:
        """``data:`` URI form — the OpenAI ``image_url`` wire shape."""
        return f"data:{self.media_type};base64,{self.base64_data}"


@runtime_checkable
class ImageResolver(Protocol):
    """Resolves a ``expert_work://image/...`` reference to image bytes."""

    async def resolve(self, ref: str) -> ResolvedImage:
        """Fetch the referenced image.

        Raises an error if the reference is malformed or the object is
        missing — the exact type is implementation-specific.
        """


@dataclass(frozen=True)
class InMemoryImageResolver:
    """:class:`ImageResolver` over a fixed ``ref -> ResolvedImage`` map — tests."""

    images: Mapping[str, ResolvedImage] = field(default_factory=dict)

    async def resolve(self, ref: str) -> ResolvedImage:
        try:
            return self.images[ref]
        except KeyError as exc:
            msg = f"no image for ref {ref!r}"
            raise KeyError(msg) from exc


@dataclass(frozen=True)
class ObjectStoreImageResolver:
    """:class:`ImageResolver` backed by an :class:`ObjectStore` — Stream J.6.

    The media type is derived from the reference's file extension:
    ``ObjectStore.get`` returns only bytes, and the upload endpoint sets
    the extension from the validated content type.
    """

    store: ObjectStore

    async def resolve(self, ref: str) -> ResolvedImage:
        image_ref = parse_image_ref(ref)
        media_type = _MEDIA_TYPE_BY_EXT.get(image_ref.ext.lower())
        if media_type is None:
            msg = f"unsupported image extension {image_ref.ext!r} in ref {ref!r}"
            raise ValueError(msg)
        data = await self.store.get(image_ref.storage_key)
        return ResolvedImage(media_type=media_type, data=data)
