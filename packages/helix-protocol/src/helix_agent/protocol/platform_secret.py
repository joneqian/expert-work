"""Platform provider/tool secret-ref records — Stream P (Mini-ADR P-7/P-8).

Runtime-managed platform credential refs (the DB overlay over the env seed).
Values are **always** ``secret://`` / ``kms://`` references — never plaintext
keys; :func:`validate_secret_ref` enforces this so plaintext can never reach
the DB, the audit log, or a response body (Mini-ADR P-8).

Naming: the harness blocks ``credentials`` paths, so this is ``platform_secret``
rather than the design's ``platform_credential`` — same surface.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from helix_agent.protocol.provider_catalog import Provider, Tool

_REF_PREFIXES = ("secret://", "kms://")


def validate_secret_ref(value: str) -> str:
    """Reject anything that is not a ``secret://`` / ``kms://`` reference."""
    if not value.startswith(_REF_PREFIXES):
        msg = "secret_ref must be a secret:// or kms:// reference, never a plaintext key"
        raise ValueError(msg)
    return value


class PlatformProviderSecretRecord(BaseModel):
    """A platform-managed LLM provider credential (secret ref), as stored."""

    model_config = ConfigDict(extra="forbid")
    provider: Provider
    secret_ref: str
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @field_validator("secret_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        return validate_secret_ref(value)


class PlatformToolSecretRecord(BaseModel):
    """A platform-managed external-tool credential (secret ref), as stored."""

    model_config = ConfigDict(extra="forbid")
    tool: Tool
    secret_ref: str
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @field_validator("secret_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        return validate_secret_ref(value)


class PlatformSecretUpsert(BaseModel):
    """Validated upsert payload — shared by the API layer and the store.

    Carries only the mutable fields; ``secret_ref`` is ref-validated so a
    plaintext key is rejected before it can be persisted (Mini-ADR P-8).
    """

    model_config = ConfigDict(extra="forbid")
    secret_ref: str
    enabled: bool = True

    @field_validator("secret_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        return validate_secret_ref(value)
