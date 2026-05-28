"""Stream O Mini-ADR O-1 — Platform Catalog startup validation.

The validator runs at create_app time + fail-fast crashes the
deployment if the supported_providers / supported_tools list does
not perfectly match the platform_*_credentials dict keys. This is
deliberately fail-fast rather than lazy-at-first-call so operators
catch misconfigs at deploy time, not at first user request.
"""

from __future__ import annotations

import pytest

from control_plane.app import _validate_platform_catalog
from control_plane.settings import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "supported_providers": [],
        "platform_provider_credentials": {},
        "supported_tools": [],
        "platform_tool_credentials": {},
    }
    base.update(overrides)
    return Settings.model_construct(**base)  # type: ignore[arg-type]


def test_empty_catalog_passes() -> None:
    _validate_platform_catalog(_settings())


def test_full_coverage_passes() -> None:
    _validate_platform_catalog(
        _settings(
            supported_providers=["anthropic", "qwen"],
            platform_provider_credentials={
                "anthropic": "kms://platform/anthropic",
                "qwen": "kms://platform/qwen",
            },
            supported_tools=["web_search"],
            platform_tool_credentials={"web_search": "kms://platform/tavily"},
        )
    )


def test_missing_provider_credential_fails() -> None:
    with pytest.raises(RuntimeError, match=r"providers in supported_providers without credentials"):
        _validate_platform_catalog(
            _settings(
                supported_providers=["anthropic", "qwen"],
                platform_provider_credentials={"anthropic": "kms://platform/anthropic"},
            )
        )


def test_extra_provider_credential_fails() -> None:
    with pytest.raises(RuntimeError, match=r"platform_provider_credentials with providers not in"):
        _validate_platform_catalog(
            _settings(
                supported_providers=["anthropic"],
                platform_provider_credentials={
                    "anthropic": "kms://platform/anthropic",
                    "qwen": "kms://platform/qwen",
                },
            )
        )


def test_missing_tool_credential_fails() -> None:
    with pytest.raises(RuntimeError, match=r"tools in supported_tools without credentials"):
        _validate_platform_catalog(
            _settings(
                supported_tools=["web_search"],
                platform_tool_credentials={},
            )
        )
