# Stream S · PR B — Backend (manifest schema + model catalog) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only control-plane endpoints the visual manifest editor needs — `GET /v1/agents/schema` (the full `AgentSpec` JSON Schema) and `GET /v1/model-catalog` (per-provider model list + capabilities, filtered to providers that have a configured + enabled platform credential).

**Architecture:** A new `MODEL_CATALOG` constant in helix-protocol (provider → models + vision/embeddings flags), mirroring the existing `PROVIDER_CATALOG`. Two new FastAPI routers in control-plane following the established `build_*_router()` + `app.include_router(...)` pattern. The schema endpoint serializes `AgentSpec.model_json_schema(by_alias=True)`. The model-catalog endpoint intersects `MODEL_CATALOG` with the configured-and-enabled providers, reusing the same per-provider source view the existing `/v1/platform/credentials` endpoint builds.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, pytest. Reference: `STREAM-S-DESIGN.md` Mini-ADRs S-1, S-4.

---

## File Structure

- **Create** `packages/helix-protocol/src/helix_agent/protocol/model_catalog.py` — `ModelEntry` dataclass-ish model + `MODEL_CATALOG: dict[Provider, tuple[ModelEntry, ...]]` + `models_for_provider()`.
- **Modify** `packages/helix-protocol/src/helix_agent/protocol/__init__.py` — export the new names.
- **Create** `services/control-plane/src/control_plane/api/agent_schema.py` — `build_agent_schema_router()` → `GET /v1/agents/schema`.
- **Create** `services/control-plane/src/control_plane/api/model_catalog.py` — `build_model_catalog_router()` → `GET /v1/model-catalog`.
- **Modify** `services/control-plane/src/control_plane/app.py` — `include_router` both new routers (near line 984, by the other `build_*_router()` calls).
- **Create** `packages/helix-protocol/tests/test_model_catalog.py` — catalog shape + `models_for_provider`.
- **Create** `services/control-plane/tests/test_agent_schema_api.py` — schema endpoint.
- **Create** `services/control-plane/tests/test_model_catalog_api.py` — catalog endpoint + provider intersection.

---

### Task 1: Model-catalog data (helix-protocol)

**Files:**
- Create: `packages/helix-protocol/src/helix_agent/protocol/model_catalog.py`
- Modify: `packages/helix-protocol/src/helix_agent/protocol/__init__.py`
- Test: `packages/helix-protocol/tests/test_model_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# packages/helix-protocol/tests/test_model_catalog.py
"""MODEL_CATALOG shape + lookup — Stream S PR B (Mini-ADR S-4)."""

from helix_agent.protocol import (
    MODEL_CATALOG,
    ModelEntry,
    models_for_provider,
)
from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG


def test_catalog_keys_are_known_providers() -> None:
    for provider in MODEL_CATALOG:
        assert provider in PROVIDER_CATALOG


def test_entries_are_model_entry_with_required_fields() -> None:
    for entries in MODEL_CATALOG.values():
        for e in entries:
            assert isinstance(e, ModelEntry)
            assert e.name
            assert isinstance(e.vision, bool)
            assert isinstance(e.embeddings, bool)


def test_deepseek_chat_present_and_not_vision() -> None:
    names = {e.name: e for e in models_for_provider("deepseek")}
    assert "deepseek-chat" in names
    assert names["deepseek-chat"].vision is False


def test_models_for_provider_excludes_deprecated() -> None:
    for e in models_for_provider("anthropic"):
        assert e.deprecated is False


def test_models_for_unknown_provider_is_empty() -> None:
    assert models_for_provider("not-a-provider") == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest packages/helix-protocol/tests/test_model_catalog.py -q`
Expected: FAIL — `ImportError: cannot import name 'MODEL_CATALOG'`.

- [ ] **Step 3: Write minimal implementation**

```python
# packages/helix-protocol/src/helix_agent/protocol/model_catalog.py
"""Per-provider model catalog — Stream S PR B (Mini-ADR S-4).

Drives the visual manifest editor's model dropdown: provider → selectable
models + capability flags. ``vision`` gates whether ``ModelSpec.supports_vision``
may be set; ``embeddings`` marks providers usable for long-term memory.

Kept current by hand (small, single source). When extending, verify the
provider's *current* in-sale model names + vision capability against the
provider's official docs — do NOT carry stale names. Mark retired models
``deprecated=True`` so they stay referenceable but drop out of the dropdown
(``models_for_provider``).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG, Provider


class ModelEntry(BaseModel):
    """One selectable model for a provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    vision: bool = False
    embeddings: bool = False
    context_window: int | None = None
    deprecated: bool = False


#: Provider → its models. Verify names/capabilities against official docs when
#: editing (Mini-ADR S-4). Entries below are seeds as of 2026-06; the PR's
#: catalog-verification step (Task 1, Step 6) confirms/extends them.
MODEL_CATALOG: dict[Provider, tuple[ModelEntry, ...]] = {
    "anthropic": (
        ModelEntry(name="claude-opus-4-1", vision=True, context_window=200_000),
        ModelEntry(name="claude-sonnet-4-5", vision=True, context_window=200_000),
        ModelEntry(name="claude-haiku-4-5-20251001", vision=True, context_window=200_000),
    ),
    "openai": (
        ModelEntry(name="gpt-4o", vision=True, context_window=128_000),
        ModelEntry(name="gpt-4o-mini", vision=True, context_window=128_000),
        ModelEntry(name="text-embedding-3-large", embeddings=True),
    ),
    "deepseek": (
        ModelEntry(name="deepseek-chat", vision=False, context_window=64_000),
        ModelEntry(name="deepseek-reasoner", vision=False, context_window=64_000),
    ),
    "kimi": (
        ModelEntry(name="moonshot-v1-128k", vision=False, context_window=128_000),
        ModelEntry(name="moonshot-v1-32k", vision=False, context_window=32_000),
    ),
    "glm": (
        ModelEntry(name="glm-4-plus", vision=False, context_window=128_000),
        ModelEntry(name="glm-4v-plus", vision=True, context_window=8_000),
    ),
    "qwen": (
        ModelEntry(name="qwen-max", vision=False, context_window=32_000),
        ModelEntry(name="qwen-vl-max", vision=True, context_window=32_000),
    ),
    "doubao": (
        ModelEntry(name="doubao-pro-32k", vision=False, context_window=32_000),
        ModelEntry(name="doubao-vision-pro-32k", vision=True, context_window=32_000),
    ),
}


def models_for_provider(provider: str) -> tuple[ModelEntry, ...]:
    """Non-deprecated models for ``provider`` (empty for unknown providers)."""
    if provider not in PROVIDER_CATALOG:
        return ()
    entries = MODEL_CATALOG.get(provider, ())  # type: ignore[arg-type]
    return tuple(e for e in entries if not e.deprecated)
```

Then add to `packages/helix-protocol/src/helix_agent/protocol/__init__.py`: import `MODEL_CATALOG, ModelEntry, models_for_provider` from `.model_catalog` and add all three to `__all__` (mirror how `provider_catalog` names are exported).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest packages/helix-protocol/tests/test_model_catalog.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Lint/type**

Run: `uv run ruff check packages/helix-protocol/src/helix_agent/protocol/model_catalog.py && uv run mypy packages/helix-protocol/src/helix_agent/protocol/model_catalog.py`
Expected: clean.

- [ ] **Step 6: Verify catalog currency (user-authorized web check)**

For each provider (anthropic, openai, deepseek, kimi/moonshot, glm/zhipu, qwen, doubao), confirm the current in-sale model names + vision capability against the provider's official docs (WebSearch is authorized for this). Fix any stale `name`/`vision`; mark retired models `deprecated=True`. Re-run Step 4.

- [ ] **Step 7: Commit**

```bash
git add packages/helix-protocol/src/helix_agent/protocol/model_catalog.py \
        packages/helix-protocol/src/helix_agent/protocol/__init__.py \
        packages/helix-protocol/tests/test_model_catalog.py
git commit -m "feat(stream-s): MODEL_CATALOG — per-provider models + capabilities (S-4)"
```

---

### Task 2: `GET /v1/agents/schema` endpoint

**Files:**
- Create: `services/control-plane/src/control_plane/api/agent_schema.py`
- Modify: `services/control-plane/src/control_plane/app.py:984` (add `include_router`)
- Test: `services/control-plane/tests/test_agent_schema_api.py`

- [ ] **Step 1: Write the failing test**

```python
# services/control-plane/tests/test_agent_schema_api.py
"""GET /v1/agents/schema — Stream S PR B (Mini-ADR S-1)."""

from fastapi.testclient import TestClient

from control_plane.api.agent_schema import build_agent_schema_router
from fastapi import FastAPI


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_agent_schema_router())
    return TestClient(app)


def test_schema_endpoint_returns_agentspec_json_schema() -> None:
    resp = _client().get("/v1/agents/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    schema = body["data"]
    # AgentSpec root: apiVersion (aliased) + kind + metadata + spec.
    assert "apiVersion" in schema["properties"]
    assert "spec" in schema["properties"]
    assert schema["properties"]["kind"]["const"] == "Agent" or "Agent" in str(
        schema["properties"]["kind"]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest services/control-plane/tests/test_agent_schema_api.py -q`
Expected: FAIL — `ModuleNotFoundError: control_plane.api.agent_schema`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/control-plane/src/control_plane/api/agent_schema.py
"""``GET /v1/agents/schema`` — the AgentSpec JSON Schema (Stream S, Mini-ADR S-1).

The visual manifest editor renders its form straight from this schema, so the
form never drifts from the backend contract. Read-only; ``by_alias=True`` emits
``apiVersion`` (the manifest's camelCase root field). Computed once at import.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from helix_agent.protocol import AgentSpec

_AGENT_SPEC_SCHEMA: dict[str, Any] = AgentSpec.model_json_schema(by_alias=True)


def build_agent_schema_router() -> APIRouter:
    router = APIRouter(prefix="/v1/agents", tags=["agents"])

    @router.get("/schema")
    async def get_agent_schema() -> dict[str, object]:
        return {"success": True, "data": _AGENT_SPEC_SCHEMA, "error": None}

    return router
```

Then in `services/control-plane/src/control_plane/app.py`, next to the other `build_*_router()` includes (~line 984), add:

```python
    app.include_router(build_agent_schema_router())
```

and add the import with the other api imports: `from control_plane.api.agent_schema import build_agent_schema_router`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest services/control-plane/tests/test_agent_schema_api.py -q`
Expected: PASS.

- [ ] **Step 5: Lint/type**

Run: `uv run ruff check services/control-plane/src/control_plane/api/agent_schema.py && uv run mypy services/control-plane/src/control_plane/api/agent_schema.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add services/control-plane/src/control_plane/api/agent_schema.py \
        services/control-plane/src/control_plane/app.py \
        services/control-plane/tests/test_agent_schema_api.py
git commit -m "feat(stream-s): GET /v1/agents/schema — AgentSpec JSON Schema (S-1)"
```

---

### Task 3: `GET /v1/model-catalog` endpoint (provider intersection)

**Files:**
- Create: `services/control-plane/src/control_plane/api/model_catalog.py`
- Modify: `services/control-plane/src/control_plane/app.py` (add `include_router`)
- Test: `services/control-plane/tests/test_model_catalog_api.py`

**Context for the executor:** "configured + enabled provider" = a provider whose platform credential source is not `unset` and is `enabled`. The existing `/v1/platform/credentials` list (`api/platform_config.py:159`) already computes per-provider source by reading `request.app.state.platform_secrets_service`. Reuse that service here; do NOT re-implement secret resolution. Read its provider view, keep only configured+enabled providers, and intersect with `MODEL_CATALOG`. Inspect `platform_config.py` lines ~159-195 for the exact service method + shape before writing Step 3; mirror its access (`_get_*` dependency that returns `request.app.state.platform_secrets_service`).

- [ ] **Step 1: Write the failing test**

```python
# services/control-plane/tests/test_model_catalog_api.py
"""GET /v1/model-catalog — Stream S PR B (Mini-ADR S-4)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api.model_catalog import build_model_catalog_router


class _FakeProviders:
    """Stands in for the platform-secrets provider view. Returns the set of
    providers that are configured + enabled."""

    def __init__(self, enabled: set[str]) -> None:
        self._enabled = enabled

    async def configured_enabled_providers(self) -> set[str]:
        return self._enabled


def _client(enabled: set[str]) -> TestClient:
    app = FastAPI()
    app.state.model_catalog_providers = _FakeProviders(enabled)
    app.include_router(build_model_catalog_router())
    return TestClient(app)


def test_lists_only_configured_enabled_providers_with_models() -> None:
    resp = _client({"deepseek"}).get("/v1/model-catalog")
    assert resp.status_code == 200
    data = resp.json()["data"]
    provs = {row["provider"] for row in data["providers"]}
    assert provs == {"deepseek"}
    ds = next(r for r in data["providers"] if r["provider"] == "deepseek")
    names = {m["name"]: m for m in ds["models"]}
    assert "deepseek-chat" in names
    assert names["deepseek-chat"]["vision"] is False


def test_empty_when_no_provider_configured() -> None:
    resp = _client(set()).get("/v1/model-catalog")
    assert resp.json()["data"]["providers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest services/control-plane/tests/test_model_catalog_api.py -q`
Expected: FAIL — `ModuleNotFoundError: control_plane.api.model_catalog`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/control-plane/src/control_plane/api/model_catalog.py
"""``GET /v1/model-catalog`` — selectable models per usable provider (Stream S,
Mini-ADR S-4).

"Usable" = the provider has a configured + enabled platform credential, so the
agent build can actually resolve a key. The visual editor's model dropdown lists
exactly these providers + their (non-deprecated) models with capability flags,
so an admin can only pick a model they have a key for. Read-only.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request

from helix_agent.protocol import models_for_provider
from helix_agent.protocol.provider_catalog import PROVIDER_CATALOG


class ConfiguredProviders(Protocol):
    async def configured_enabled_providers(self) -> set[str]: ...


def _get_providers(request: Request) -> ConfiguredProviders:
    return request.app.state.model_catalog_providers  # type: ignore[no-any-return]


def build_model_catalog_router() -> APIRouter:
    router = APIRouter(prefix="/v1/model-catalog", tags=["agents"])

    @router.get("")
    async def get_model_catalog(
        providers: Annotated[ConfiguredProviders, Depends(_get_providers)],
    ) -> dict[str, object]:
        usable = await providers.configured_enabled_providers()
        rows = [
            {
                "provider": p,
                "models": [m.model_dump(mode="json") for m in models_for_provider(p)],
            }
            for p in PROVIDER_CATALOG
            if p in usable and models_for_provider(p)
        ]
        return {"success": True, "data": {"providers": rows}, "error": None}

    return router
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest services/control-plane/tests/test_model_catalog_api.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire the real provider source in app.py**

In `services/control-plane/src/control_plane/app.py`: (a) `from control_plane.api.model_catalog import build_model_catalog_router`; (b) `app.include_router(build_model_catalog_router())` by the others; (c) set `app.state.model_catalog_providers` to a small adapter that implements `configured_enabled_providers()` by reading the resolved platform-secrets service (the same object `platform_config` uses). Inspect `platform_config.py` ~159-195 for the method that yields per-provider source/enabled; the adapter returns the set of providers whose source != "unset" and enabled is true. Keep the adapter in `api/model_catalog.py` as a small class `PlatformConfiguredProviders` taking the service; add a focused unit test for it against the in-memory platform secret store.

- [ ] **Step 6: Run control-plane suite + lint/type**

Run: `uv run python -m pytest services/control-plane/tests/test_model_catalog_api.py services/control-plane/tests/test_agent_schema_api.py -q && uv run ruff check services/control-plane/src/control_plane/api/model_catalog.py && uv run mypy services/control-plane/src/control_plane/api/model_catalog.py`
Expected: PASS + clean.

- [ ] **Step 7: Commit**

```bash
git add services/control-plane/src/control_plane/api/model_catalog.py \
        services/control-plane/src/control_plane/app.py \
        services/control-plane/tests/test_model_catalog_api.py
git commit -m "feat(stream-s): GET /v1/model-catalog — usable providers + models (S-4)"
```

---

### Task 4: App-level smoke + preflight

**Files:**
- Test: `services/control-plane/tests/test_app_smoke.py` (extend if present, else a focused new test)

- [ ] **Step 1: Write a smoke test that the wired app serves both endpoints**

```python
# services/control-plane/tests/test_stream_s_endpoints_smoke.py
"""Both Stream S endpoints are wired into the real app (Stream S PR B)."""

from fastapi.testclient import TestClient

from control_plane.app import create_app


def test_schema_and_catalog_are_wired() -> None:
    client = TestClient(create_app())
    assert client.get("/v1/agents/schema").status_code in (200, 401)
    assert client.get("/v1/model-catalog").status_code in (200, 401)
```

> 200 if these endpoints are unauthenticated, 401 if the app's auth middleware guards all `/v1/*`. Either is fine for "wired"; the point is the route exists (not 404). If both return 404, the `include_router` wiring is missing.

- [ ] **Step 2: Run it**

Run: `uv run python -m pytest services/control-plane/tests/test_stream_s_endpoints_smoke.py -q`
Expected: PASS.

- [ ] **Step 3: Full preflight**

Run:
```bash
uv run ruff check packages/helix-protocol services/control-plane/src/control_plane/api/agent_schema.py services/control-plane/src/control_plane/api/model_catalog.py
uv run python -m pytest packages/helix-protocol/tests/test_model_catalog.py services/control-plane/tests/test_agent_schema_api.py services/control-plane/tests/test_model_catalog_api.py services/control-plane/tests/test_stream_s_endpoints_smoke.py -q
uv run mypy packages
```
Expected: all clean/green. (Note: CI `mypy packages` includes tests — keep test helper signatures annotated.)

- [ ] **Step 4: Commit + push + PR**

```bash
git add services/control-plane/tests/test_stream_s_endpoints_smoke.py
git commit -m "test(stream-s): smoke — schema + model-catalog endpoints wired"
git push -u origin stream-s/b-backend
gh pr create --base main --title "feat(stream-s): PR B — manifest schema + model catalog endpoints" --body "..."
```

---

## Self-Review (completed)

- **Spec coverage (S-1, S-4):** S-1 schema endpoint → Task 2; S-4 model catalog + `/v1/model-catalog` intersection → Tasks 1+3; catalog currency → Task 1 Step 6. ✓
- **Placeholders:** none — all code shown. The two places the executor must inspect-then-write (the platform-secrets provider view in Task 3 Step 5; the PR body in Task 4) are explicitly flagged with where to look, not silent TODOs.
- **Type consistency:** `ModelEntry` fields (name/vision/embeddings/context_window/deprecated), `models_for_provider`, `configured_enabled_providers()` used identically across tasks. `AgentSpec.model_json_schema(by_alias=True)` matches the agent_spec.py:733 root (alias `apiVersion`).
- **Scope:** backend-only, independently testable; C/D/E planned separately after B ships.
