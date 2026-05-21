"""J.6.补强-4 (Mini-ADR J-34) — EXIF strip on upload.

Camera images carry an EXIF block with GPS coordinates, device
identifiers, timestamps, and software fingerprints — a low-cost
metadata exfiltration channel if uploads sit in tenant object stores
that get shared, exported, or backed up. Strip the block before the
bytes leave the request handler.

The mime allowlist already excludes SVG (no XML / script surface), so
we only worry about the raster formats Pillow understands. PNG / JPEG /
WebP / GIF all flow through ``Image.open`` round-tripping cleanly. Any
parse error short-circuits to ``ValueError`` — the uploads handler
maps that to ``400 invalid image``.
"""

from __future__ import annotations

from io import BytesIO
from typing import Final

from PIL import Image, UnidentifiedImageError

#: Pillow format name keyed by the upload's MIME type — keeps the
#: round-trip in the same format the user uploaded so the bytes match
#: their original perceptually.
_PIL_FORMAT_BY_MIME: Final[dict[str, str]] = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}


class ImageSanitizeError(ValueError):
    """The uploaded bytes failed to parse / re-encode through Pillow.

    Raised by :func:`strip_exif` so the uploads handler can map to
    ``400 invalid image`` rather than a 500 (Pillow exceptions vary by
    format).
    """


def strip_exif(raw: bytes, *, mime_type: str) -> bytes:
    """Return ``raw`` re-encoded **without** EXIF / metadata.

    Mini-ADR J-34. Pillow's ``Image.save(..., exif=b"")`` drops the
    EXIF block in formats that carry one (JPEG / WebP); for PNG / GIF
    we strip the ``info`` metadata dict before re-encoding. Either
    way the output bytes carry zero ancillary metadata.

    :param raw: the uploaded bytes (already size-validated upstream).
    :param mime_type: the upload's claimed MIME (from the allowlist —
        anything outside ``_PIL_FORMAT_BY_MIME`` is a programmer error
        because the uploads handler validates against the allowlist
        first).
    :raises ImageSanitizeError: bytes don't parse, or the re-encode
        path the format requires isn't available.
    """
    pil_format = _PIL_FORMAT_BY_MIME.get(mime_type)
    if pil_format is None:
        msg = f"strip_exif: unsupported mime_type {mime_type!r}"
        raise ImageSanitizeError(msg)
    try:
        with Image.open(BytesIO(raw)) as image:
            # Force a full decode so partially-read streams surface here
            # as ``ImageSanitizeError`` rather than later in ``save``.
            image.load()
            out = BytesIO()
            # ``info`` carries PNG text chunks + GIF transparency etc.;
            # wiping it removes ancillary metadata that ``exif=b""``
            # doesn't reach.
            image.info.clear()
            # The ``save`` call mirrors Pillow's documented EXIF-strip
            # idiom — passing an empty ``exif`` bytes object overrides
            # the source's block.
            image.save(out, format=pil_format, exif=b"")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        msg = f"strip_exif: cannot re-encode image as {pil_format}"
        raise ImageSanitizeError(msg) from exc
