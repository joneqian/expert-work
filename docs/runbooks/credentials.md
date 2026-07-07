# Credentials & Provider Catalog Runbook

> Stream O — Credentials & Provider Catalog (PR B).
> Design: [`docs/streams/STREAM-O-DESIGN.md`](../streams/STREAM-O-DESIGN.md).
> Implementation:
> [`packages/expert-work-common/src/expert_work/common/credentials/resolver.py`](../../packages/expert-work-common/src/expert_work/common/credentials/resolver.py)
> + control-plane gates in
> [`api/tenant_config.py`](../../services/control-plane/src/control_plane/api/tenant_config.py)
> and [`api/agents.py`](../../services/control-plane/src/control_plane/api/agents.py).

## 1. Concepts

* **Platform Catalog** — the deployment-time opt-in list of LLM
  providers + external tools the platform supports. Lives in
  `settings.supported_providers` / `settings.supported_tools`. Each
  opted-in entry **must** have a matching credential in
  `platform_provider_credentials` / `platform_tool_credentials`, or
  the control-plane refuses to boot (Mini-ADR O-1 fail-fast).

* **Tenant credentials_mode** — each tenant is in exactly one of two
  modes:
  * `platform` (default) — every LLM provider / tool call uses the
    platform's secret_ref. Billing flows to the platform's accounts.
  * `tenant` — every LLM provider / tool call uses the tenant's own
    secret_ref. Billing flows to the tenant's accounts. Tenant must
    have configured credentials for every provider / tool that any
    of their agents reference.

* **All-or-nothing** — no mixing. A tenant in `tenant` mode cannot
  pull embedding from the platform and main-model from their own
  account. Switching from `platform` to `tenant` requires the
  tenant has already configured credentials for every used provider
  and tool — otherwise the switch returns 403 with the missing list
  (Mini-ADR O-4).

## 2. Platform setup (ops)

In each deployment environment (dev / staging / prod):

1. **Pick supported providers.** Default ships with
   `["anthropic", "openai", "qwen"]`. Add more by setting
   `EXPERT_WORK_SUPPORTED_PROVIDERS=anthropic,openai,qwen,deepseek`
   (comma-separated env var, parsed as JSON-compatible list).
2. **Pick supported tools.** Default ships with `["web_search"]`.
3. **Configure platform credentials** for every supported provider
   and tool:
   ```bash
   export EXPERT_WORK_PLATFORM_PROVIDER_CREDENTIALS='{
     "anthropic": "kms://platform/llm/anthropic-key",
     "openai":    "kms://platform/llm/openai-key",
     "qwen":      "kms://platform/llm/qwen-key"
   }'
   export EXPERT_WORK_PLATFORM_TOOL_CREDENTIALS='{
     "web_search": "kms://platform/tools/tavily-key"
   }'
   ```
4. **Deploy.** Startup validator (`_validate_platform_catalog`) fails
   fast if any opted-in entry has no credential, or if any credential
   entry is for a non-opted provider/tool.

## 3. Diagnostic: `ExpertWorkUpliftCredentialsResolveFailureSpike`

**Trigger**: `Expert Work:uplift:credentials_resolve_failure_rate:5m > 0.1`
for 10m for some `(role, key, mode)` combination.

The alert label tells you which mode is failing:

* **`mode=platform`** — platform secret_ref is missing or invalid.
  Re-check `platform_provider_credentials` / `platform_tool_credentials`
  matches `supported_providers` / `supported_tools`. The startup
  validator should have caught this; if it didn't, someone added an
  entry to supported_* at runtime via a hot-reload (don't do this).

* **`mode=tenant`** — the tenant is in `tenant` mode but no credential
  is configured for `{key}`. Two paths to fix:
  1. **Tenant action**: Admin UI → Settings → Credentials → add a
     `kms://...` URI for `{role}/{key}`. The tenant continues in
     tenant mode with the new credential.
  2. **Operator action**: if the tenant requests rollback, PATCH
     `/v1/tenants/{tenant_id}/config` with `credentials_mode=platform`.
     Future calls resolve against the platform catalog; tenant's
     stored credentials are preserved but not used.

The `CREDENTIALS_RESOLVE_FAILED` audit row carries `mode + role + key`
in `details` so you can trace the failed lookup back to the originating
caller (agent run, consolidator tick, etc.).

## 4. Diagnostic: `ExpertWorkUpliftLegacyCredentialsFallbackPresent`

**Trigger**: `Expert Work:uplift:legacy_credentials_fallback_rate:1d > 0`
for 1d for some `role`.

Stream O PR B keeps the legacy settings fields
(`embedding_api_key_ref` / `rerank_api_key_ref` / `tavily_api_key_ref`)
as deprecation-marked fallbacks. The remaining callers
(`resolve_embedder`, `resolve_reranker`, `web_search` tool factory)
still read them directly because per-tenant migration is invasive and
deferred to Stream O PR 2 + PR 3.

This alert is a **soft P3 reminder** that the migration is not yet
complete. After PR 2 lands and the legacy fields are removed from
settings, this rate should drop to zero. If still non-zero 30 days
after PR 2 merge, the migration regressed somewhere — grep for
`embedding_api_key_ref` to find the offending caller.

## 5. Switching a tenant to `tenant` credentials_mode

Steps from operator perspective:

1. **Inventory the tenant's used providers/tools** by listing their
   agents and reading each manifest's `spec.model.provider` (plus
   `spec.vision.model.provider`,
   `spec.policies.memory_consolidation.aux_model.provider`, and the
   fallback chains). Also note any agent using `web_search` tool.
2. **Pre-stage credentials** via:
   ```bash
   curl -X PUT https://control-plane/v1/tenants/{tenant_id}/config \
     -H 'Authorization: Bearer <admin-token>' \
     -d '{
       "model_credentials_ref": {
         "anthropic": "kms://acme/anthropic",
         "openai":    "kms://acme/openai"
       },
       "tool_credentials": {
         "web_search": "kms://acme/tavily"
       }
     }'
   ```
3. **Flip the mode** via a follow-up PATCH:
   ```bash
   curl -X PUT https://control-plane/v1/tenants/{tenant_id}/config \
     -H 'Authorization: Bearer <admin-token>' \
     -d '{"credentials_mode": "tenant"}'
   ```
   If the merged credentials cover every used provider/tool, the
   switch succeeds (200) and takes effect immediately for the next
   LLM call.
   If credentials are missing, the API returns 403 with a body like:
   ```json
   {
     "code": "CREDENTIALS_MODE_SWITCH_INCOMPLETE",
     "message": "cannot switch to tenant credentials_mode: missing credentials for providers=['qwen'] tools=[]",
     "missing_providers": ["qwen"],
     "missing_tools": []
   }
   ```
   Add the missing credential(s) via step 2 and retry step 3.

## 6. Sprint #7 aux model wire (Stream O PR B's primary caller win)

Sprint #7's `MemoryConsolidator` previously used the
`_NullConsolidatorAuxModel` placeholder that returned hardcoded JSON
(`false_cluster` for clusters, `durable` for lone items) and produced
zero consolidations. Stream O PR B wires the production
`LLMRouterAuxModelAdapter`:

```
Consolidator worker tick
  → _consolidate_or_reject(tenant_id, ...)
  → aux_model.__call__(prompt, model=None, tenant_id)
  → CredentialsResolver.resolve_provider(tenant_id, default_provider)
  → SecretStore.get(secret_ref)
  → build_llm_router(spec, secret_store)
  → router(messages=[HumanMessage(prompt)], tools=[])
  → ConsolidatorLLMReply(text, model, input_tokens, output_tokens)
```

When the platform's default provider
(`memory_consolidator_default_aux_provider`, default `anthropic`)
has a platform credential, the adapter activates. Otherwise the
control-plane falls back to `_NullConsolidatorAuxModel` with a
log warning — the worker runs idle until ops adds the credential.

## 7. M1 follow-ups (not in PR B)

* **PR 2** — migrate `resolve_embedder` / `resolve_reranker` /
  `web_search` tool factory to use `CredentialsResolver`. Per-tenant
  embedding calls require the Embedder protocol to accept a
  `tenant_id` arg; this is invasive (touches memory writeback,
  knowledge ingestion, DLQ worker). Stream O PR 2.
* **PR 3** — MCP servers integration. MCP server config has more
  shape than a single `secret_ref`; the mode model adapts but the
  schema needs its own ADR. Stream O PR 3.
* **Admin UI Credentials panel** — Stream H follow-up; design lives
  in STREAM-O-DESIGN § 2.7 (Mini-ADR O-7).
