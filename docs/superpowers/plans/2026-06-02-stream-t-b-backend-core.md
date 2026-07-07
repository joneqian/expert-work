# Stream T PR B — Backend Core (platform embedding config + dynamic resolver) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the platform embedding/rerank provider+model a DB-stored, runtime-changeable setting, and resolve the embedder/reranker dynamically so an admin's change takes effect without restart — keeping long-term memory gated on "platform embedding configured".

**Architecture:** A single-row `platform_embedding_config` table + `PlatformEmbeddingConfigService` (DB-wins / env-fallback / TTL cache). The embedder/reranker become `DynamicResolvingEmbedder` / `DynamicResolvingReranker` that read the current config at `embed()`/`rerank()` time (so every existing consumer — agent memory nodes, memory-CRUD API, knowledge retriever, ingestion, DLQ, consolidator — picks up changes for free). The build-time "memory needs embedding" gate moves into control-plane's `make_agent_builder` (which can see the config service); the orchestrator stays decoupled.

**Tech Stack:** Python 3.x, SQLAlchemy + Alembic, Pydantic, pytest. Backend only (no UI, no HTTP API — those are PR C/D). Design: `docs/streams/STREAM-T-DESIGN.md` (Mini-ADRs T-1/T-2/T-3).

**Required models (user-specified 2026-06-02):** the model catalog must mark these so PR C/D can list them — embedding: GLM `embedding-3`, Qwen `text-embedding-v4`; rerank: Qwen `qwen3-vl-rerank`. (OpenAI `text-embedding-3-large` already present — keep.)

---

## File Structure

**New**
- `packages/expert-work-persistence/migrations/versions/00XX_platform_embedding_config.py` — single-row table (pick the next free revision id; ≤32 chars, e.g. `0051_platform_embed_config`).
- `packages/expert-work-persistence/src/expert_work/persistence/models/platform_embedding_config.py` — SQLAlchemy model.
- `packages/expert-work-persistence/src/expert_work/persistence/platform_embedding_config/{base,sql,memory}.py` — store protocol + SQL + in-memory impls (mirror an existing platform store).
- `services/control-plane/src/control_plane/platform_embedding_config.py` — `PlatformEmbeddingConfigService` (DB-wins/env-fallback/TTL).
- Tests: `packages/expert-work-protocol/tests/test_model_catalog.py` (extend), `services/control-plane/tests/test_platform_embedding_config_service.py`, `services/control-plane/tests/test_dynamic_resolver.py`.

**Modified**
- `packages/expert-work-protocol/src/expert_work/protocol/model_catalog.py` — add `rerank: bool` to `ModelEntry`; add the 3 required models.
- `services/control-plane/src/control_plane/runtime.py` — `DynamicResolvingEmbedder`, `DynamicResolvingReranker`; `make_agent_builder` build-time embedding gate.
- `services/control-plane/src/control_plane/app.py` — construct the config service + dynamic embedder/reranker; swap into the existing wiring.
- `services/control-plane/src/control_plane/settings.py` — keep env fields as the fallback (no change needed beyond confirming they're read by the service).

---

## Conventions (verified)
- CI: `mypy` does NOT cover control-plane/src (local-only there); `pytest -m "not integration"`; run from repo root `uv run python -m pytest <path>`. Protocol signature changes must be swept across tools/eval doubles (memory `reference_protocol_sweep_includes_tools_eval`).
- Alembic revision id ≤ 32 chars (memory `feedback_alembic_revision_id_32_chars`); set `down_revision` to the current head (find via `ls packages/expert-work-persistence/migrations/versions/ | sort | tail`).
- Platform tables are tenant-less + bypass-RLS for writes — mirror `platform_provider_secret` (migration `0049_platform_secrets.py`) and `SqlPlatformSecretStore`.
- TTL-cache pattern: mirror `PlatformSecretsService` (`platform_secrets.py:38-58`) — 30s TTL, invalidate on write.
- No side effects in asserts (memory `feedback_no_side_effect_in_assert`); no print/log of secret-named vars (memory `feedback_codeql_clear_text_logging_secret_name`); no module-level unused names (memory `feedback_codeql_unused_global`).

---

### Task 1: Catalog — add `rerank` flag + the required embedding/rerank models

**Files:**
- Modify: `packages/expert-work-protocol/src/expert_work/protocol/model_catalog.py`
- Test: `packages/expert-work-protocol/tests/test_model_catalog.py`

- [ ] **Step 1: Write failing tests** — append to `test_model_catalog.py`:
```python
def test_required_embedding_and_rerank_models_present() -> None:
    glm = {e.name: e for e in MODEL_CATALOG["glm"]}
    qwen = {e.name: e for e in MODEL_CATALOG["qwen"]}
    assert glm["embedding-3"].embeddings is True
    assert qwen["text-embedding-v4"].embeddings is True
    assert qwen["qwen3-vl-rerank"].rerank is True


def test_model_entry_has_rerank_flag_defaulting_false() -> None:
    e = ModelEntry(name="x")
    assert e.rerank is False
```

- [ ] **Step 2: Run — confirm FAIL.** `uv run python -m pytest packages/expert-work-protocol/tests/test_model_catalog.py -q`

- [ ] **Step 3: Add the `rerank` field to `ModelEntry`** (after `embeddings`):
```python
    embeddings: bool = False
    rerank: bool = False
```
Update the `ModelEntry` docstring/`embeddings` comment to mention `rerank` marks rerank-capable models (platform rerank config, Stream T).

- [ ] **Step 4: Add the required models.** In the `"glm"` tuple add (current-tier, not deprecated):
```python
        ModelEntry(name="embedding-3", embeddings=True),
```
In the `"qwen"` tuple add:
```python
        ModelEntry(name="text-embedding-v4", embeddings=True),
        ModelEntry(name="qwen3-vl-rerank", rerank=True),
```
Add an inline comment on each noting it's the platform embedding/rerank model (Stream T, user-specified). Keep `text-embedding-3-large` under openai.

- [ ] **Step 5: Run — confirm PASS** (new + existing catalog tests). Also run the model-catalog API tests to confirm `model_dump` still serialises (the new field flows through): `uv run python -m pytest packages/expert-work-protocol/tests/test_model_catalog.py services/control-plane/tests/test_model_catalog_api.py -q`

- [ ] **Step 6: Commit**
```bash
git add packages/expert-work-protocol/src/expert_work/protocol/model_catalog.py packages/expert-work-protocol/tests/test_model_catalog.py
git commit -m "feat(stream-t): catalog rerank flag + embedding-3/text-embedding-v4/qwen3-vl-rerank (PR B)"
```

---

### Task 2: `platform_embedding_config` table + model + store

**Files:**
- Create: migration `00XX_platform_embed_config.py`, `models/platform_embedding_config.py`, `platform_embedding_config/{base,sql,memory}.py`
- Test: `packages/expert-work-persistence/tests/test_platform_embedding_config_store.py`

**Schema (single-row singleton):** columns `id` (TEXT PK, constant `"singleton"`), `embedding_provider` (TEXT, nullable), `embedding_model` (TEXT, nullable), `rerank_provider` (TEXT, nullable), `rerank_model` (TEXT, nullable), `updated_at` (timestamptz), `updated_by` (TEXT, nullable). Tenant-less, no RLS. A NULL/absent row means "not configured (fall back to env)".

- [ ] **Step 1: Read an existing platform store to mirror.** Read `packages/expert-work-persistence/.../models/` for the `platform_provider_secret` model and its store (`SqlPlatformSecretStore`) + the `0049_platform_secrets.py` migration. Match table-definition style, the `bypass_rls`/tenant-less convention, and the store protocol+sql+memory split.

- [ ] **Step 2: Write the failing store test** `packages/expert-work-persistence/tests/test_platform_embedding_config_store.py` (use the in-memory store):
```python
import pytest
from expert_work.persistence.platform_embedding_config.memory import InMemoryPlatformEmbeddingConfigStore

@pytest.mark.asyncio
async def test_get_returns_none_when_unset() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    assert await store.get() is None

@pytest.mark.asyncio
async def test_put_then_get_round_trips() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(
        embedding_provider="qwen", embedding_model="text-embedding-v4",
        rerank_provider="qwen", rerank_model="qwen3-vl-rerank", updated_by="admin-1",
    )
    row = await store.get()
    assert row is not None
    assert row.embedding_provider == "qwen"
    assert row.embedding_model == "text-embedding-v4"
    assert row.rerank_provider == "qwen"
    assert row.rerank_model == "qwen3-vl-rerank"

@pytest.mark.asyncio
async def test_put_is_idempotent_singleton() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(embedding_provider="glm", embedding_model="embedding-3", rerank_provider=None, rerank_model=None, updated_by="a")
    await store.put(embedding_provider="qwen", embedding_model="text-embedding-v4", rerank_provider=None, rerank_model=None, updated_by="b")
    row = await store.get()
    assert row is not None and row.embedding_provider == "qwen"  # last write wins, one row
```

- [ ] **Step 3: Run — confirm FAIL.** `uv run python -m pytest packages/expert-work-persistence/tests/test_platform_embedding_config_store.py -q`

- [ ] **Step 4: Implement** the protocol (`base.py` — `get() -> Row | None`, `put(*, embedding_provider, embedding_model, rerank_provider, rerank_model, updated_by) -> None`, a frozen `PlatformEmbeddingConfigRow` dataclass), the in-memory store (`memory.py`), the SQLAlchemy model (`models/platform_embedding_config.py`), the SQL store (`sql.py`, upsert-on-singleton via `bypass_rls_session`), and the migration (`00XX`, `down_revision` = current head). Follow the `platform_provider_secret` patterns from Step 1.

- [ ] **Step 5: Run — confirm PASS (3).**

- [ ] **Step 6: Commit**
```bash
git add packages/expert-work-persistence/migrations/versions/00XX_platform_embed_config.py packages/expert-work-persistence/src/expert_work/persistence/models/platform_embedding_config.py packages/expert-work-persistence/src/expert_work/persistence/platform_embedding_config/ packages/expert-work-persistence/tests/test_platform_embedding_config_store.py
git commit -m "feat(stream-t): platform_embedding_config table + store (PR B)"
```

---

### Task 3: `PlatformEmbeddingConfigService` (DB-wins / env-fallback / TTL cache)

**Files:**
- Create: `services/control-plane/src/control_plane/platform_embedding_config.py`
- Test: `services/control-plane/tests/test_platform_embedding_config_service.py`

**Contract:**
```python
class PlatformEmbeddingConfigService:
    def __init__(self, *, store, settings, ttl_seconds: float = 30.0, clock=...): ...
    async def effective_embedding_config(self) -> tuple[Provider, str] | None: ...
    async def effective_rerank_config(self) -> tuple[Provider, str] | None: ...
    def invalidate(self) -> None: ...
```
- DB row present + `embedding_provider`/`embedding_model` both set → return `(provider, model)`. Else fall back to `settings.embedding_provider`/`settings.embedding_model` IF that provider is in `settings.effective_supported_providers` (else `None`). Rerank analogous (rerank is optional → may be `None`).
- TTL-cache the resolved tuples; `invalidate()` clears (called by the PR C write path).

- [ ] **Step 1: Read** `services/control-plane/src/control_plane/platform_secrets.py:38-58` for the exact TTL-cache idiom (cache field + monotonic clock + invalidate). Mirror it. Read `settings.py:142-175` + `effective_supported_providers` for the fallback fields.

- [ ] **Step 2: Write the failing test** `test_platform_embedding_config_service.py`:
```python
import pytest
from expert_work.persistence.platform_embedding_config.memory import InMemoryPlatformEmbeddingConfigStore
from control_plane.platform_embedding_config import PlatformEmbeddingConfigService

class _Settings:
    embedding_provider = "qwen"
    embedding_model = "text-embedding-v4"
    rerank_provider = "qwen"
    rerank_model = "qwen3-vl-rerank"
    effective_supported_providers = ("qwen", "openai")

@pytest.mark.asyncio
async def test_falls_back_to_env_when_no_db_row() -> None:
    svc = PlatformEmbeddingConfigService(store=InMemoryPlatformEmbeddingConfigStore(), settings=_Settings())
    assert await svc.effective_embedding_config() == ("qwen", "text-embedding-v4")

@pytest.mark.asyncio
async def test_db_row_wins_over_env() -> None:
    store = InMemoryPlatformEmbeddingConfigStore()
    await store.put(embedding_provider="glm", embedding_model="embedding-3", rerank_provider=None, rerank_model=None, updated_by="a")
    svc = PlatformEmbeddingConfigService(store=store, settings=_Settings())
    assert await svc.effective_embedding_config() == ("glm", "embedding-3")
    assert await svc.effective_rerank_config() is None  # db row sets rerank None → no rerank

@pytest.mark.asyncio
async def test_env_fallback_none_when_provider_unsupported() -> None:
    class S(_Settings):
        embedding_provider = "doubao"  # not in supported
    svc = PlatformEmbeddingConfigService(store=InMemoryPlatformEmbeddingConfigStore(), settings=S())
    assert await svc.effective_embedding_config() is None
```

- [ ] **Step 3: Run — confirm FAIL.**

- [ ] **Step 4: Implement** the service per the contract (TTL cache mirroring `PlatformSecretsService`). Decision: a DB row that sets `embedding_*` but leaves `rerank_*` NULL means "embedding configured, rerank off" — `effective_rerank_config()` returns `None`. Env fallback for rerank only applies when there's NO db row at all.

- [ ] **Step 5: Run — confirm PASS (3).**

- [ ] **Step 6: Commit**
```bash
git add services/control-plane/src/control_plane/platform_embedding_config.py services/control-plane/tests/test_platform_embedding_config_service.py
git commit -m "feat(stream-t): PlatformEmbeddingConfigService DB-wins/env-fallback/TTL (PR B)"
```

---

### Task 4: `DynamicResolvingEmbedder` / `DynamicResolvingReranker`

**Files:**
- Modify: `services/control-plane/src/control_plane/runtime.py`
- Test: `services/control-plane/tests/test_dynamic_resolver.py`

Mirror `ResolvingEmbedder`/`ResolvingReranker` (runtime.py:241-299) but read `(provider, model)` from the config service at call time.

- [ ] **Step 1: Write the failing test** `test_dynamic_resolver.py`. Use a fake config service + fake resolver/secret_store; assert the embedder reads the CURRENT config each call (change config between calls → second embed uses the new provider/model), and that a `None` config raises a clear error. For the reranker, assert `None` config degrades to identity order (no raise). Drive it with small fakes (no network) — pattern off existing `runtime` tests if present; otherwise stub `OpenAICompatibleEmbedder`/`build_llm_router` via monkeypatch. (Implementer: choose the lightest seam — e.g. inject the delegate factory — and document it.)

- [ ] **Step 2: Run — confirm FAIL.**

- [ ] **Step 3: Implement** in `runtime.py`:
```python
@dataclass(frozen=True)
class DynamicResolvingEmbedder:
    """Embedder that reads the current platform embedding config per call so
    an admin's change takes effect without restart (Stream T, Mini-ADR T-3)."""
    config_service: PlatformEmbeddingConfigService
    resolver: CredentialsResolver
    secret_store: SecretStore

    async def embed(self, texts: Sequence[str], *, tenant_id: UUID) -> list[tuple[float, ...]]:
        if not texts:
            return []
        cfg = await self.config_service.effective_embedding_config()
        if cfg is None:
            raise AgentFactoryError(
                "platform embedding is not configured — configure it in platform settings"
            )
        provider, model = cfg
        secret_ref = await self.resolver.resolve_provider(tenant_id=tenant_id, provider=provider)
        api_key = await self.secret_store.get(parse_secret_ref(secret_ref))
        delegate = OpenAICompatibleEmbedder(client=HTTPEmbeddingClient(api_key=api_key), model=model)
        return await delegate.embed(texts, tenant_id=tenant_id)
```
and `DynamicResolvingReranker` mirroring `ResolvingReranker` but: read `effective_rerank_config()`; if `None` → return identity `list(range(len(documents)))[:top_k]` (degrade); else resolve+rerank as today. (Keep the old `ResolvingEmbedder`/`ResolvingReranker` + `resolve_embedder`/`resolve_reranker` for now if other call sites use them; remove only the ones this PR orphans — verify with grep.)

- [ ] **Step 4: Run — confirm PASS.** Confirm the "config changed between calls → new provider used" assertion passes (proves dynamic).

- [ ] **Step 5: Commit**
```bash
git add services/control-plane/src/control_plane/runtime.py services/control-plane/tests/test_dynamic_resolver.py
git commit -m "feat(stream-t): DynamicResolvingEmbedder/Reranker read live platform config (PR B)"
```

---

### Task 5: Wire the dynamic resolver + build-time gate into the app

**Files:**
- Modify: `services/control-plane/src/control_plane/app.py`, `services/control-plane/src/control_plane/runtime.py` (`make_agent_builder`)
- Test: `services/control-plane/tests/test_*` (extend an existing app/agent-build test; add a focused gate test)

**Two changes:**
1. **app.py wiring** (around the current `resolve_embedder`/`resolve_reranker` block, app.py:660-682 + MemoryEnv:696 + builder:719 + consumers at 737/754/800): construct `platform_embedding_config_service = PlatformEmbeddingConfigService(store=..., settings=resolved_settings)`, then `embedder = DynamicResolvingEmbedder(config_service, credentials_resolver, resolved_secret_store)` and `reranker = DynamicResolvingReranker(...)`. Keep passing `embedder`/`reranker` to every existing consumer unchanged (MemoryEnv, knowledge_retriever, app.state.embedder, ingestion, DLQ, consolidator) — they now read live config for free. Expose `app.state.platform_embedding_config_service` for PR C's write path.
2. **Build-time gate** in control-plane `make_agent_builder._build` (runtime.py:160): it has `tenant_id` + access to the config service (pass it in). Before building, if `spec.spec.memory` declares `long_term` and `await config_service.effective_embedding_config() is None` → raise `AgentFactoryError("manifest declares memory.long_term but platform embedding is not configured")`. This is the real gate (the orchestrator `env.embedder is None` check at agent_factory.py:888 stays as defense but won't fire since the dynamic embedder is non-None).

- [ ] **Step 1: Read** app.py:580-810 (the lifespan embedder/reranker/MemoryEnv/builder/consumer wiring) and runtime.py:160 `make_agent_builder` signature + its `_build` closure. Map exactly what to swap. Note every consumer of `embedder`.

- [ ] **Step 2: Write a failing gate test.** A control-plane test that builds an agent whose manifest declares `memory.long_term` while the config service returns `None` for embedding → expect `AgentFactoryError`; and the same with embedding configured → builds (memory nodes present). Model it on existing `make_agent_builder` / agent-build tests (grep `make_agent_builder` in `services/control-plane/tests`).

- [ ] **Step 3: Run — confirm FAIL.**

- [ ] **Step 4: Implement** the wiring swap + the gate. `make_agent_builder` gains a `platform_embedding_config_service` parameter (thread it from app.py); `_build` does the gate check. Keep MemoryEnv receiving the dynamic embedder.

- [ ] **Step 5: Run the focused test + a broad control-plane sweep** to catch wiring breakage: `uv run python -m pytest services/control-plane/tests -q -m "not integration"`. Fix any consumer that assumed `embedder is None` semantics (e.g. memory-CRUD 503 guard at app.py:670, ingestion/DLQ/consolidator `if embedder is not None` guards — with a dynamic embedder always non-None, decide per-consumer: for consolidator/ingestion/DLQ keep them enabled, OR gate their startup on `await config_service.effective_embedding_config() is not None`; document the choice). Keep the change minimal and correct.

- [ ] **Step 6: Commit**
```bash
git add services/control-plane/src/control_plane/app.py services/control-plane/src/control_plane/runtime.py services/control-plane/tests/
git commit -m "feat(stream-t): wire dynamic embedder/reranker + build-time embedding gate (PR B)"
```

---

## Final verification (before opening the PR)
- [ ] `uv run python -m pytest packages/expert-work-protocol/tests/test_model_catalog.py packages/expert-work-persistence/tests/test_platform_embedding_config_store.py services/control-plane/tests -q -m "not integration"` — green.
- [ ] Protocol sweep: grep the repo (incl. `tools/`, `eval/`) for any `ResolvingEmbedder(`/`resolve_embedder(` call sites this PR orphaned; none should remain broken.
- [ ] `uv run ruff check <changed dirs>` clean; `uv run pre-commit run --files <changed>` clean.
- [ ] Confirm the migration applies on a fresh DB (in-memory/sqlite path used by the suite) and the revision id is ≤32 chars with `down_revision` = prior head.

PR title: `feat(stream-t): PR B — platform embedding config table + service + dynamic resolver`. Body: link the design; note catalog now carries the 3 required models + `rerank` flag; the embedder is dynamic (live config, no restart); the memory gate moved to control-plane `make_agent_builder`; HTTP API + UI are PR C/D.

---

## Self-Review

**Spec coverage (STREAM-T-DESIGN §5 PR B = "table + service (env fallback) + DynamicResolvingEmbedder/Reranker + app wiring; 单测"):**
- Table → Task 2. Service (DB-wins/env-fallback/TTL) → Task 3. Dynamic resolver → Task 4. App wiring + gate → Task 5. ✅
- Catalog (the 3 user-specified models + `rerank` flag) → Task 1 — needed by PR C validation + PR D dropdowns; foundational, so it lands first in B. ✅
- Build-time gate relocation (Mini-ADR T-5 layer ②) → Task 5. ✅
- Deferred (correct): HTTP API `GET/PUT /v1/platform/embedding-config` = PR C; UI = PR D; memory-on default template + PR D revert = PR E.

**Placeholder scan:** the `00XX` migration id is a "next free revision" pointer (Task 2 resolves it against the head); Task 4's test seam ("choose the lightest seam") is a bounded decision with criteria, not a placeholder; everything with code shows the code. The wiring tasks (5) intentionally say "read X then adapt" for app.py/make_agent_builder internals not fully quoted here — driven by concrete gate tests — matching the verified-against-reality pattern used in prior streams.

**Type consistency:** `effective_embedding_config()`/`effective_rerank_config()` return `tuple[Provider,str]|None` consistently across Tasks 3/4/5. `PlatformEmbeddingConfigService` ctor `(store, settings)` consistent. `DynamicResolvingEmbedder(config_service, resolver, secret_store)` matches the Task 5 app wiring. The store `put(*, embedding_provider, embedding_model, rerank_provider, rerank_model, updated_by)` signature matches across Tasks 2/3 tests. `ModelEntry.rerank` (Task 1) is what PR C/D will filter on.
