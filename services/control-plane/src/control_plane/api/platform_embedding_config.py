"""``/v1/platform/embedding-config`` — platform embedding/rerank selection (Stream T).

system_admin-only view of the EFFECTIVE platform embedding + rerank
provider/model selection, alongside the selectable options derived from the
catalog filtered to configured providers (so the admin UI fills both dropdowns
in a single call). This module hosts the GET (read) surface; the PUT (write)
surface lives alongside it (Task 3).

Gating mirrors :mod:`control_plane.api.platform_config`: ``principal``
arrives via the shared :func:`control_plane.api._authz._principal` dependency
and handlers gate inline on ``principal.is_system_admin`` (platform-level; no
RBAC ``tenant`` resource — same precedent as ``platform_config.py``). Responses
use the ``{"success", "data", "error"}`` envelope.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.api._authz import _principal
from control_plane.platform_embedding_config import PlatformEmbeddingConfigService
from control_plane.platform_secrets import PlatformSecretsService
from helix_agent.protocol import PROVIDER_CATALOG, Principal, models_for_provider


def _require_system_admin(principal: Principal) -> None:
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLATFORM_SCOPE_FORBIDDEN",
                "message": "only a system admin may manage the platform embedding config",
            },
        )


def _get_embedding_config_service(request: Request) -> PlatformEmbeddingConfigService:
    return request.app.state.platform_embedding_config_service  # type: ignore[no-any-return]


def _get_secrets_service(request: Request) -> PlatformSecretsService:
    return request.app.state.platform_secrets_service  # type: ignore[no-any-return]


def _pair_to_dict(pair: tuple[str, str] | None) -> dict[str, str] | None:
    if pair is None:
        return None
    provider, model = pair
    return {"provider": provider, "model": model}


def _available(configured: set[str], *, kind: str) -> list[dict[str, str]]:
    """Catalog options for ``configured`` providers, filtered by capability.

    ``kind`` is ``"embeddings"`` or ``"rerank"`` — the ``ModelEntry`` flag that
    must be ``True`` for the model to be selectable.
    """
    options: list[dict[str, str]] = []
    for provider in PROVIDER_CATALOG:
        if provider not in configured:
            continue
        for entry in models_for_provider(provider):
            if getattr(entry, kind) is True:
                options.append({"provider": provider, "model": entry.name})
    return options


def build_platform_embedding_config_router() -> APIRouter:
    router = APIRouter(prefix="/v1/platform/embedding-config", tags=["platform_config"])

    @router.get("")
    async def get_platform_embedding_config(
        principal: Annotated[Principal, Depends(_principal)],
        embedding_config_service: Annotated[
            PlatformEmbeddingConfigService, Depends(_get_embedding_config_service)
        ],
        secrets_service: Annotated[PlatformSecretsService, Depends(_get_secrets_service)],
    ) -> dict[str, object]:
        """Effective embedding/rerank selection + the selectable options.

        ``embedding`` / ``rerank`` are ``{"provider", "model"}`` or ``null``;
        ``available_embedding`` / ``available_rerank`` list the capable catalog
        models for every configured platform provider."""
        _require_system_admin(principal)
        embedding = await embedding_config_service.effective_embedding_config()
        rerank = await embedding_config_service.effective_rerank_config()
        configured = set(await secrets_service.effective_provider_credentials())
        return {
            "success": True,
            "data": {
                "embedding": _pair_to_dict(embedding),
                "rerank": _pair_to_dict(rerank),
                "available_embedding": _available(configured, kind="embeddings"),
                "available_rerank": _available(configured, kind="rerank"),
            },
            "error": None,
        }

    return router
