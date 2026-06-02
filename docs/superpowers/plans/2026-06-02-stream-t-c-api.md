# Stream T PR C — Platform Embedding Config API (GET/PUT + validation + audit) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose `GET/PUT /v1/platform/embedding-config` so a system admin reads + sets the platform embedding/rerank provider+model (PR D's UI consumes this), with validation (model ∈ catalog with the right capability flag; provider key configured) and an audit trail; writes invalidate the dynamic-resolver cache so they take effect immediately.

**Architecture:** A new `build_platform_embedding_config_router()` mirroring `build_platform_config_router()` (`api/platform_config.py`) — same `_require_system_admin` gate, `{success,data,error}` envelope, audit-emit pattern. Reads/writes go through `PlatformEmbeddingConfigService` (PR B, on `app.state.platform_embedding_config_service`); "is the provider key configured" is membership in `PlatformSecretsService.effective_provider_credentials()` (on `app.state.platform_secrets_service`); model validity comes from `models_for_provider()` + the `embeddings`/`rerank` flags (PR B catalog).

**Tech Stack:** FastAPI, Pydantic, pytest. Backend only (UI = PR D). Design: `docs/streams/STREAM-T-DESIGN.md` (Mini-ADR T-4).

---

## Conventions (verified, mirror these)
- `api/platform_config.py`: `_require_system_admin(principal)` (403 `PLATFORM_SCOPE_FORBIDDEN`); `_principal` dep; `_get_audit`; `service.invalidate()` after writes; `_emit_platform_audit(audit, ...)` (read its real signature at the credentials PUT, ~line 243, and reuse it or the same `emit`/`AuditLogger` pattern); envelope `{ "success": True, "data": ..., "error": None }`; 422 with `{"code","message"}` detail on validation failure.
- `AuditAction` is a single `StrEnum` in `packages/helix-protocol/src/helix_agent/protocol/audit.py` (imported everywhere — NOT a double Literal; still `grep -rn "PLATFORM_SECRET" ` to confirm there's no second copy before adding).
- Catalog: `from helix_agent.protocol import models_for_provider, PROVIDER_CATALOG`; an entry is embedding-capable iff `.embeddings`, rerank-capable iff `.rerank` (PR B). `models_for_provider(p)` returns non-deprecated entries.
- App wiring: `app.state.platform_embedding_config_service` (PR B), `app.state.platform_secrets_service`, `app.state.audit_logger`, `app.state.settings` already set. The router is registered next to `build_platform_config_router()` in `app.py` (grep `build_platform_config_router` for the include site).
- **Run `uv run pre-commit run --files <changed>` (NOT just `ruff check`) before every commit** — pre-commit also runs `ruff-format`; CI fails if formatting differs. Tests: `uv run python -m pytest <path> -q -m "not integration"` from repo root.

---

### Task 1: `PLATFORM_EMBEDDING_CONFIG_UPDATED` audit action + `PlatformEmbeddingConfigService.put()`

**Files:**
- Modify: `packages/helix-protocol/src/helix_agent/protocol/audit.py` (add enum member)
- Modify: `services/control-plane/src/control_plane/platform_embedding_config.py` (add `put`)
- Test: extend `services/control-plane/tests/test_platform_embedding_config_service.py`

- [ ] **Step 1: Add the audit action.** In `audit.py`'s `AuditAction(StrEnum)`, add a member next to the other `PLATFORM_*` actions (grep `PLATFORM_` in that file to match naming; e.g. `PLATFORM_EMBEDDING_CONFIG_UPDATED = "platform.embedding_config.updated"`). Confirm via grep there's no second Literal copy of audit actions to update.

- [ ] **Step 2: Write the failing service test** — append to `test_platform_embedding_config_service.py`:
```python
@pytest.mark.asyncio
async def test_put_writes_and_invalidates() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    svc = PlatformEmbeddingConfigService(store=store, settings=_Settings())
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")  # env, cached
    await svc.put(embedding_provider="glm", embedding_model="embedding-3", rerank_provider=None, rerank_model=None, updated_by="admin-1")
    # put must invalidate the cache so the new DB row is visible immediately
    assert await svc.effective_embedding_config() == ("glm", "embedding-3")
    assert await svc.effective_rerank_config() is None
```

- [ ] **Step 3: Run — confirm FAIL.** `uv run python -m pytest services/control-plane/tests/test_platform_embedding_config_service.py -q`

- [ ] **Step 4: Implement `put`** on the service:
```python
    async def put(self, *, embedding_provider, embedding_model, rerank_provider, rerank_model, updated_by) -> None:
        await self._store.put(
            embedding_provider=embedding_provider, embedding_model=embedding_model,
            rerank_provider=rerank_provider, rerank_model=rerank_model, updated_by=updated_by,
        )
        self.invalidate()
```
(Match the store's `put` signature from PR B.)

- [ ] **Step 5: Run — confirm PASS** (existing + new). 

- [ ] **Step 6: pre-commit + commit**
```bash
uv run pre-commit run --files packages/helix-protocol/src/helix_agent/protocol/audit.py services/control-plane/src/control_plane/platform_embedding_config.py services/control-plane/tests/test_platform_embedding_config_service.py
git add -A && git commit -m "feat(stream-t): embedding-config audit action + service.put (PR C)"
```

---

### Task 2: `GET /v1/platform/embedding-config` + router wiring

**Files:**
- Create: `services/control-plane/src/control_plane/api/platform_embedding_config.py`
- Modify: `services/control-plane/src/control_plane/app.py` (include the router)
- Test: `services/control-plane/tests/test_platform_embedding_config_api.py`

**GET response** (envelope `data`): the current selection + the selectable options (so PR D fills dropdowns in one call):
```json
{ "embedding": {"provider": "qwen", "model": "text-embedding-v4"} | null,
  "rerank": {"provider": "qwen", "model": "qwen3-vl-rerank"} | null,
  "available_embedding": [{"provider": "<p>", "model": "<m>"} ...],
  "available_rerank": [{"provider": "<p>", "model": "<m>"} ...] }
```
`available_*` = for each provider in `PROVIDER_CATALOG` whose key is configured (in `platform_secrets_service.effective_provider_credentials()`), its `models_for_provider(p)` entries with `.embeddings` (resp. `.rerank`). `embedding`/`rerank` = `effective_embedding_config()` / `effective_rerank_config()` as `{provider, model}` or `null`.

- [ ] **Step 1: Read** `api/platform_config.py` lines 51-211 (the `_principal`/`_require_system_admin`/`_get_audit` deps + the GET handler + envelope). Mirror the dep + envelope style. Note how `_principal` is imported.

- [ ] **Step 2: Write the failing API test** `test_platform_embedding_config_api.py`. Build a FastAPI app/TestClient the same way the existing platform-config API test does (grep `test_platform_config*` under `services/control-plane/tests` for the harness: how it sets `app.state.platform_*_service`, a system_admin principal, etc.). Test:
  - GET as system_admin returns 200 with `embedding`/`rerank` reflecting the configured service + `available_embedding` containing only configured-provider embedding models.
  - GET as non-admin → 403.
Mirror the existing platform-config API test's app construction + principal injection exactly.

- [ ] **Step 3: Run — confirm FAIL.**

- [ ] **Step 4: Implement** `build_platform_embedding_config_router()` (prefix `/v1/platform/embedding-config`) with the GET handler per the response shape. Deps read `app.state.platform_embedding_config_service` + `app.state.platform_secrets_service` + `app.state.settings`/catalog. `_require_system_admin` first. Then register the router in `app.py` next to `build_platform_config_router()`.

- [ ] **Step 5: Run — confirm PASS.** Also run a broad control-plane sweep to confirm wiring didn't break app startup: `uv run python -m pytest services/control-plane/tests -q -m "not integration" -k "platform or app or smoke"`.

- [ ] **Step 6: pre-commit + commit** (run `pre-commit run --files` on the new api file + app.py + test).
```bash
git add -A && git commit -m "feat(stream-t): GET /v1/platform/embedding-config + router (PR C)"
```

---

### Task 3: `PUT /v1/platform/embedding-config` + validation + audit (core)

**Files:**
- Modify: `services/control-plane/src/control_plane/api/platform_embedding_config.py`
- Test: extend `services/control-plane/tests/test_platform_embedding_config_api.py`

**PUT body** (Pydantic, `extra="forbid"`): `embedding_provider: str`, `embedding_model: str`, `rerank_provider: str | None = None`, `rerank_model: str | None = None`. (rerank is all-or-nothing: both set or both None — validate.)
**Validation (422 `{"code","message"}` on failure):**
- `embedding_provider` key configured (in `platform_secrets_service.effective_provider_credentials()`); else `code="EMBEDDING_PROVIDER_KEY_MISSING"`, message guiding to configure the key first.
- `embedding_model` ∈ `models_for_provider(embedding_provider)` with `.embeddings is True`; else `code="INVALID_EMBEDDING_MODEL"`.
- If rerank given: same key check (`RERANK_PROVIDER_KEY_MISSING`) + `rerank_model` has `.rerank is True` (`INVALID_RERANK_MODEL`). If exactly one of rerank_provider/rerank_model set → 422 `INVALID_RERANK_PAIR`.
**On success:** `service.put(..., updated_by=principal.subject-or-equivalent)`; emit audit (`PLATFORM_EMBEDDING_CONFIG_UPDATED`, resource = the platform embedding config, NO secret values — only provider+model names); return the new effective config in the envelope.

- [ ] **Step 1: Write failing tests** (append): valid embedding-only PUT → 200 + GET reflects it; PUT with unconfigured provider → 422 `EMBEDDING_PROVIDER_KEY_MISSING`; PUT with a non-embedding model (e.g. a chat model) → 422 `INVALID_EMBEDDING_MODEL`; PUT with rerank pair valid → 200; PUT non-admin → 403; assert an audit row was emitted (mirror how the platform-config API test asserts audit, if it does) and that NO secret value appears.
- [ ] **Step 2: Run — confirm FAIL.**
- [ ] **Step 3: Implement** the PUT handler + the request model + the validation helpers. Reuse `_require_system_admin`, the audit emit pattern from `platform_config.py` (read `_emit_platform_audit`'s real signature and call it the same way). `models_for_provider` + flag checks for model validity; `effective_provider_credentials()` keyset for key-configured.
- [ ] **Step 4: Run — confirm PASS** + broad sweep `-k "platform or embedding"`.
- [ ] **Step 5: pre-commit + commit**
```bash
git add -A && git commit -m "feat(stream-t): PUT /v1/platform/embedding-config + validation + audit (PR C)"
```

---

## Final verification
- [ ] `uv run python -m pytest services/control-plane/tests -q -m "not integration"` green (broad — catches wiring/app breakage).
- [ ] `uv run pre-commit run --files <all changed>` clean (ruff + ruff-format + whitespace).
- [ ] Audit row carries only provider/model names, never a key value.

PR title: `feat(stream-t): PR C — platform embedding-config API (GET/PUT + validation + audit)`. Body: link design; note GET returns current + available options (PR D dropdown source); PUT validates key-configured + model capability + rerank pairing; writes invalidate the cache (immediate effect). UI = PR D; memory-on default + PR D hint revert = PR E.

---

## Self-Review
**Spec coverage (T-3/T-4):** GET (current + available) → Task 2; PUT (validation: key-configured + model-capability + rerank-pair; audit; cache-invalidate) → Tasks 1+3; audit action → Task 1; system_admin gate → all (mirrors `_require_system_admin`). Deferred correctly: build-time gate (PR B, done), create-agent UI block + memory-on default + PR D hint revert (PR E), the config UI (PR D).
**Placeholders:** the GET/PUT bodies are fully specified; "mirror the existing platform-config API test harness" is a read-and-match directive (the harness is real, in-repo) not a placeholder — the same pattern PR B used. `_emit_platform_audit` signature is read-from-source.
**Type consistency:** `service.put(*, embedding_provider, embedding_model, rerank_provider, rerank_model, updated_by)` (Task 1) matches the PUT call (Task 3) and the store `put` (PR B). `effective_provider_credentials()` keyset for key-checks. `models_for_provider().embeddings/.rerank` flags (PR B catalog). Envelope shape consistent with `platform_config.py`.
