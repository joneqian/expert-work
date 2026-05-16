"""``CredentialProxySettings`` — env-driven knobs for the F.5 service."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CredentialProxySettings(BaseSettings):
    """Resolved runtime settings; cheap to construct in tests."""

    model_config = SettingsConfigDict(
        env_prefix="HELIX_CRED_PROXY_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "credential_proxy"
    log_level: str = "INFO"

    # --------------------------------------------------------------- listen
    host: str = "0.0.0.0"  # noqa: S104 - internal service, bound inside the cluster
    port: int = Field(default=8080, gt=0, le=65535)

    # ------------------------------------------------------------------ db
    db_dsn: str = "postgresql+asyncpg://helix_agent:helix_agent_dev@localhost:5432/helix_agent_dev"
    db_echo: bool = False

    # -------------------------------------------------------- secret store
    #: SecretStore backend (ADR-0007). ``local_dev`` for dev / test;
    #: ``aliyun_kms`` once the deploy-time backend is wired.
    secret_store_backend: str = "local_dev"  # noqa: S105 — a backend name, not a secret
    secret_store_env_file: str | None = None

    # ----------------------------------------------------------- secret cache
    #: In-process LRU over resolved secrets — keeps SecretStore read QPS
    #: down (subsystems/11 § 9 M0: flat 60s TTL).
    cache_max_size: int = Field(default=10_000, gt=0)
    cache_ttl_s: float = Field(default=60.0, gt=0, le=3600)

    # -------------------------------------------------------------- upstream
    upstream_timeout_s: float = Field(default=60.0, gt=0, le=600)
