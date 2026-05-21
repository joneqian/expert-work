"""Tests for :mod:`control_plane.api._image_sanitize` — J.6.补强-4 / Mini-ADR J-34."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from control_plane.api._image_sanitize import ImageSanitizeError, strip_exif


def _png_with_metadata() -> bytes:
    """A PNG carrying ancillary text chunks (the kind that exfil
    arbitrary strings through ``image.info``)."""
    buf = BytesIO()
    image = Image.new("RGB", (2, 2), color=(255, 0, 0))
    image.info["Comment"] = "tenant-secret-leak"  # Pillow lifts this into a tEXt chunk
    image.save(buf, format="PNG", pnginfo=_pnginfo({"Comment": "tenant-secret-leak"}))
    return buf.getvalue()


def _pnginfo(items: dict[str, str]) -> object:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for k, v in items.items():
        info.add_text(k, v)
    return info


def _jpeg_with_exif() -> bytes:
    """A JPEG carrying a synthetic EXIF block — sentinel bytes the
    sanitiser must remove."""
    buf = BytesIO()
    image = Image.new("RGB", (4, 4), color=(0, 255, 0))
    # EXIF bytes 0xFFE1 marker + payload starting with the sentinel.
    image.save(buf, format="JPEG", exif=b"helix-test-exif-secret")
    return buf.getvalue()


def test_strip_exif_removes_png_text_chunks() -> None:
    raw = _png_with_metadata()
    assert b"tenant-secret-leak" in raw  # PNG carries the text chunk

    sanitised = strip_exif(raw, mime_type="image/png")
    assert b"tenant-secret-leak" not in sanitised
    # Still a valid PNG.
    with Image.open(BytesIO(sanitised)) as image:
        image.load()
        assert image.size == (2, 2)


def test_strip_exif_removes_jpeg_exif() -> None:
    raw = _jpeg_with_exif()
    assert b"helix-test-exif-secret" in raw

    sanitised = strip_exif(raw, mime_type="image/jpeg")
    assert b"helix-test-exif-secret" not in sanitised
    with Image.open(BytesIO(sanitised)) as image:
        image.load()
        assert image.size == (4, 4)


def test_strip_exif_rejects_unknown_mime() -> None:
    with pytest.raises(ImageSanitizeError, match="unsupported mime_type"):
        strip_exif(b"any", mime_type="application/octet-stream")


def test_strip_exif_rejects_unparseable_bytes() -> None:
    with pytest.raises(ImageSanitizeError):
        strip_exif(b"not-an-image", mime_type="image/png")
