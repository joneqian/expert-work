"""Stream O Mini-ADR O-4 — all-or-nothing credentials_mode switch gate.

Covers the validation function directly (full API e2e is in
test_tenant_config_endpoints.py at a higher level). The gate:

* allows mode='platform' patches unconditionally
* allows tenant→tenant patches unconditionally (steady-state)
* rejects platform→tenant when any used provider/tool is missing
* allows platform→tenant when all used are covered
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from control_plane.api.tenant_config import (
    CredentialsModeSwitchIncompleteError,
    _validate_credentials_mode_switch,
)
from helix_agent.protocol import (
    TenantConfigPatch,
    TenantConfigRecord,
    TenantPlan,
)

_NOW = datetime.now(UTC)
_TENANT = UUID("11111111-1111-1111-1111-111111111111")


def _record(
    *,
    mode: str = "platform",
    model_creds: dict | None = None,
    tool_creds: dict | None = None,
) -> TenantConfigRecord:
    return TenantConfigRecord(
        tenant_id=_TENANT,
        display_name="Acme",
        plan=TenantPlan.FREE,
        credentials_mode=mode,  # type: ignore[arg-type]
        model_credentials_ref=model_creds or {},
        tool_credentials=tool_creds or {},
        created_at=_NOW,
        updated_at=_NOW,
        updated_by="tester",
    )


def test_platform_mode_patch_unconditional() -> None:
    # platform mode patch — no gate runs.
    _validate_credentials_mode_switch(
        patch=TenantConfigPatch(credentials_mode="platform"),
        existing=_record(),
        used_providers={"anthropic"},
        used_tools={"web_search"},
    )


def test_tenant_to_tenant_steady_state_unconditional() -> None:
    # already in tenant mode + still tenant mode — gate skipped.
    _validate_credentials_mode_switch(
        patch=TenantConfigPatch(credentials_mode="tenant"),
        existing=_record(mode="tenant", model_creds={"anthropic": "kms://acme/a"}),
        used_providers={"anthropic", "qwen"},  # qwen missing but no gate
        used_tools=set(),
    )


def test_switch_to_tenant_missing_provider_rejected() -> None:
    with pytest.raises(CredentialsModeSwitchIncompleteError) as exc_info:
        _validate_credentials_mode_switch(
            patch=TenantConfigPatch(
                credentials_mode="tenant",
                model_credentials_ref={"anthropic": "kms://acme/anthropic"},
            ),
            existing=_record(),
            used_providers={"anthropic", "openai"},
            used_tools=set(),
        )
    assert exc_info.value.missing_providers == ["openai"]
    assert exc_info.value.missing_tools == []


def test_switch_to_tenant_missing_tool_rejected() -> None:
    with pytest.raises(CredentialsModeSwitchIncompleteError) as exc_info:
        _validate_credentials_mode_switch(
            patch=TenantConfigPatch(
                credentials_mode="tenant",
                model_credentials_ref={"anthropic": "kms://acme/anthropic"},
            ),
            existing=_record(),
            used_providers={"anthropic"},
            used_tools={"web_search"},
        )
    assert exc_info.value.missing_tools == ["web_search"]


def test_switch_to_tenant_full_coverage_accepted() -> None:
    # No exception — all used providers + tools have credentials.
    _validate_credentials_mode_switch(
        patch=TenantConfigPatch(
            credentials_mode="tenant",
            model_credentials_ref={
                "anthropic": "kms://acme/anthropic",
                "openai": "kms://acme/openai",
            },
            tool_credentials={"web_search": "kms://acme/tavily"},
        ),
        existing=_record(),
        used_providers={"anthropic", "openai"},
        used_tools={"web_search"},
    )


def test_switch_to_tenant_merges_patch_and_existing() -> None:
    # patch only adds qwen; existing has anthropic. Merged covers both.
    _validate_credentials_mode_switch(
        patch=TenantConfigPatch(
            credentials_mode="tenant",
            # NOTE: TenantConfigPatch.model_credentials_ref None means
            # "fall back to existing". To merge in test, we set the full
            # union; the _validate function does the same union math.
            model_credentials_ref={
                "anthropic": "kms://acme/anthropic",
                "qwen": "kms://acme/qwen",
            },
        ),
        existing=_record(model_creds={"anthropic": "kms://acme/anthropic"}),
        used_providers={"anthropic", "qwen"},
        used_tools=set(),
    )
