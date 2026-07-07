"""Tests for the J.6 image reference parser."""

from __future__ import annotations

from uuid import UUID

import pytest

from expert_work.protocol.multimodal import IMAGE_REF_PREFIX, ImageRef, parse_image_ref

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_THREAD = UUID("22222222-2222-2222-2222-222222222222")
_IMAGE = UUID("33333333-3333-3333-3333-333333333333")


def test_to_uri_round_trips_with_extension() -> None:
    ref = ImageRef(tenant_id=_TENANT, thread_id=_THREAD, image_id=_IMAGE, ext=".png")
    assert parse_image_ref(ref.to_uri()) == ref


def test_to_uri_round_trips_without_extension() -> None:
    ref = ImageRef(tenant_id=_TENANT, thread_id=_THREAD, image_id=_IMAGE)
    parsed = parse_image_ref(ref.to_uri())
    assert parsed == ref
    assert parsed.ext == ""


def test_storage_key_follows_adr_0004_convention() -> None:
    ref = ImageRef(tenant_id=_TENANT, thread_id=_THREAD, image_id=_IMAGE, ext=".jpeg")
    assert ref.storage_key == f"{_TENANT}/uploads/{_THREAD}/{_IMAGE}.jpeg"


def test_to_uri_renders_canonical_scheme() -> None:
    ref = ImageRef(tenant_id=_TENANT, thread_id=_THREAD, image_id=_IMAGE, ext=".webp")
    assert ref.to_uri() == f"{IMAGE_REF_PREFIX}{_TENANT}/{_THREAD}/{_IMAGE}.webp"


def test_parse_extracts_every_component() -> None:
    ref = parse_image_ref(f"{IMAGE_REF_PREFIX}{_TENANT}/{_THREAD}/{_IMAGE}.gif")
    assert ref.tenant_id == _TENANT
    assert ref.thread_id == _THREAD
    assert ref.image_id == _IMAGE
    assert ref.ext == ".gif"


def test_parse_rejects_wrong_scheme() -> None:
    with pytest.raises(ValueError, match="must start with"):
        parse_image_ref(f"s3://image/{_TENANT}/{_THREAD}/{_IMAGE}.png")


def test_parse_rejects_too_few_segments() -> None:
    with pytest.raises(ValueError, match="<tenant>/<thread>/<image>"):
        parse_image_ref(f"{IMAGE_REF_PREFIX}{_TENANT}/{_IMAGE}.png")


def test_parse_rejects_too_many_segments() -> None:
    with pytest.raises(ValueError, match="<tenant>/<thread>/<image>"):
        parse_image_ref(f"{IMAGE_REF_PREFIX}{_TENANT}/{_THREAD}/extra/{_IMAGE}.png")


def test_parse_rejects_non_uuid_tenant() -> None:
    with pytest.raises(ValueError, match="tenant / thread segment"):
        parse_image_ref(f"{IMAGE_REF_PREFIX}not-a-uuid/{_THREAD}/{_IMAGE}.png")


def test_parse_rejects_non_uuid_image_id() -> None:
    with pytest.raises(ValueError, match="image-id segment"):
        parse_image_ref(f"{IMAGE_REF_PREFIX}{_TENANT}/{_THREAD}/not-a-uuid.png")


def test_parse_rejects_malformed_extension() -> None:
    with pytest.raises(ValueError, match="malformed extension"):
        parse_image_ref(f"{IMAGE_REF_PREFIX}{_TENANT}/{_THREAD}/{_IMAGE}.PNG!")
