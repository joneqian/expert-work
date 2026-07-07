"""MCP connector catalog env-seed — Stream MCP-OAUTH (OA-5).

Lets the platform declare its oauth2 connectors in a JSON template
(``configs/mcp-catalog-seed.json``) whose ``oauth_client_id`` placeholders —
``${MCP_OAUTH_<NAME>_CLIENT_ID}`` — resolve from the environment. A connector
whose placeholder isn't set yet is simply skipped, so the platform boots before
the OAuth app is registered; once the operator fills the env var and restarts,
the connector is created.

Two functions, split so the parsing is pure and unit-testable:

* :func:`load_catalog_seed` — parse + resolve placeholders → validated upserts.
  A *missing* env placeholder skips that entry (expected, pre-registration); a
  malformed template or an invalid entry raises :class:`CatalogSeedError`
  (fail-fast — the template itself is wrong).
* :func:`seed_catalog` — idempotent create-if-absent against the store.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping

from pydantic import ValidationError

from control_plane.tenant_scope import bypass_rls_session
from expert_work.persistence import (
    McpConnectorCatalogAlreadyExistsError,
    McpConnectorCatalogStore,
)
from expert_work.protocol import McpConnectorCatalogUpsert

logger = logging.getLogger("expert_work.control_plane.catalog_seed")

_SEED_ACTOR = "catalog-seed"
# ``${VAR}`` placeholder — upper-snake env var names only.
_PLACEHOLDER = re.compile(r"\$\{([A-Z0-9_]+)\}")


class CatalogSeedError(Exception):
    """The seed template is malformed or describes an invalid catalog entry."""


def _resolve_placeholders(value: str, env: Mapping[str, str]) -> tuple[str, list[str]]:
    """Substitute ``${VAR}`` from ``env``; return (resolved, missing_var_names)."""
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        if var in env and env[var] != "":
            return env[var]
        missing.append(var)
        return match.group(0)

    return _PLACEHOLDER.sub(_sub, value), missing


def _resolve_entry(
    entry: dict[str, object], env: Mapping[str, str]
) -> tuple[dict[str, object], list[str]]:
    """Resolve placeholders across an entry's string fields (one level deep)."""
    resolved: dict[str, object] = {}
    missing: list[str] = []
    for key, val in entry.items():
        if isinstance(val, str):
            new_val, miss = _resolve_placeholders(val, env)
            resolved[key] = new_val
            missing.extend(miss)
        else:
            resolved[key] = val
    return resolved, missing


def load_catalog_seed(
    raw: str, env: Mapping[str, str]
) -> tuple[list[McpConnectorCatalogUpsert], list[str]]:
    """Parse the seed JSON and resolve ``${VAR}`` placeholders from ``env``.

    Returns ``(ready, skipped)`` — validated upserts ready to create, and the
    names of entries skipped for an unset placeholder. Raises
    :class:`CatalogSeedError` on malformed JSON or an invalid entry.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CatalogSeedError(f"seed file is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise CatalogSeedError("seed file must be a JSON array of catalog entries")

    ready: list[McpConnectorCatalogUpsert] = []
    skipped: list[str] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise CatalogSeedError(f"seed entry #{index} is not an object")
        name = entry.get("name", f"#{index}")
        resolved, missing = _resolve_entry(entry, env)
        if missing:
            skipped.append(str(name))
            continue
        try:
            ready.append(McpConnectorCatalogUpsert(**resolved))
        except ValidationError as exc:
            raise CatalogSeedError(f"seed entry '{name}' is invalid: {exc}") from exc
    return ready, skipped


async def seed_catalog(
    *, store: McpConnectorCatalogStore, entries: list[McpConnectorCatalogUpsert]
) -> tuple[list[str], list[str]]:
    """Idempotently create absent entries; skip those already present.

    Returns ``(created, existing)`` connector names. Runs under
    ``bypass_rls_session`` — catalog rows are platform-global (NULL tenant).
    """
    created: list[str] = []
    existing: list[str] = []
    async with bypass_rls_session():
        for upsert in entries:
            if await store.get_by_name(upsert.name) is not None:
                existing.append(upsert.name)
                continue
            try:
                await store.create(upsert=upsert, actor_id=_SEED_ACTOR)
                created.append(upsert.name)
            except McpConnectorCatalogAlreadyExistsError:
                # Raced with another replica seeding the same name — benign.
                existing.append(upsert.name)
    return created, existing
