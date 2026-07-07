# Stream O — Credentials & Provider Catalog(设计先行)

> 引入**统一的模型供应商 / 工具凭证管理面**:平台维护支持的 provider/tool 白名单
> + 平台级 API key;租户在 `platform` 或 `tenant` 两种 mode 之间二选一,**all-or-nothing**
> 决定所有 LLM/tool 调用的凭证来源。
>
> **起因**:Capability Uplift Sprint #7 凝结引擎 ship 后发现"系统模块的 LLM 配置"
> 分散在 4 个层(env / tenant_config / agent manifest / 各 caller 各自处理),
> 无统一面 + 没有"租户用自己模型"的合规通道。Sprint #7 aux 模型 wire 是
> Stream O PR 1 的第一个 caller 落地点。

设计先行规则([memory:design-first-iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_design_first_iteration.md)):
**任何一行 backend 代码落地之前**,先完成本设计文档 + Mini-ADR 锁定。

---

## 0. 背景与范围澄清(2026-05-28 用户拍板)

### 0.1 现状(分散的 4 层)

```
1. 平台级默认(env / settings.py)— 每模块各自加字段
   embedding_model + embedding_api_key_ref                ← J.3 memory
   rerank_model + rerank_api_key_ref                      ← J.5 knowledge
   tavily_api_key_ref                                     ← web_search 工具
   memory_consolidator_default_aux_model                  ← Sprint #7 新加
   (context_compressor summariser:还没有字段)

2. 租户级覆盖(tenant_config.model_credentials_ref)
   {provider → "kms://..."}                               ← 只能改 API key,不能换模型,不能 mode 切换

3. agent 级覆盖(agent manifest)
   spec.model = ModelSpec(...)                            ← 主模型
   policies.memory_consolidation.aux_model: ModelSpec     ← Sprint #7 新加

4. 调用层(LLMRouter)
   只服务 agent 主调用 — fallback / retry / breaker
   embedding/rerank/aux/tools 都各走各的 client,不复用
```

### 0.2 4 个关键决策(用户 2026-05-28 拍板)

| # | 决策 | 选定 | 反对方案为何弃 |
|---|------|------|-----------------|
| 1 | 模型名锁的颗粒度 | **A**:平台只锁 provider,具体 model 名 tenant/agent 可选 | B(同时锁 model 名清单)— 任何新模型要管理员审批,运维负担太重 |
| 2 | 切换 mode 的校验时机 | **A**:切换 API 时强制校验"已用 provider 凭证齐全",否则 403 | B(允许立即切,运行期 401)— 运行期才暴露问题,租户已经在跑的 agent 突然挂 |
| 3 | mode 切换对运行中 agent 的影响 | **A**:立即生效(LLMRouter 调用前每次解析,无 caching) | B(仅新 session)/ C(完全重建 cache)— 跟 Stream C.7 现有"立即生效"模型一致 |
| 4 | MCP servers 是否纳入本 Stream | **B**:独立另算,本 Stream 只覆盖 LLM provider + tool API key | A(同 Stream 收口)— MCP 配置复杂(servers list + env + secret),独立 PR 更聚焦,Stream O PR 3 再收 |

### 0.3 全局原则

1. **凭证 vs 模型名分离** — `credentials_mode` 只决定 "凭证从哪来" / "billing 找谁",**不影响**模型名的选择
2. **All-or-nothing 在凭证层强制** — 没有"embedding 用平台 key,主模型用租户 key"的混搭
3. **Provider/Tool 白名单** — 租户在 `tenant` mode 下只能给**平台已支持的** provider/tool 配凭证;不允许加平台没注册的新 provider
4. **凭证缺失硬失败** — `tenant` mode 下租户没配某 provider 凭证 → API 调用阶段 401 fail-fast,**不静默回退到平台**

---

## 1. 范围 & 边界

### 1.1 In-scope(PR 1)

| 子项 | 实现内容 | 关联 |
|------|---------|------|
| **O.1 Platform Catalog** | settings 加 `supported_providers: list[Provider]` + `platform_provider_credentials: dict[Provider, str]` + `supported_tools: list[Tool]` + `platform_tool_credentials: dict[Tool, str]`;启动时校验:enabled provider 必须 ∈ supported | Mini-ADR O-1 |
| **O.2 tenant_config 扩展** | 加 `credentials_mode: Literal["platform", "tenant"] = "platform"` + 重组 `model_credentials_ref` → 严格按 Provider 白名单校验 + 新加 `tool_credentials_ref: dict[Tool, str]`;migration 0047 | Mini-ADR O-2 |
| **O.3 CredentialsResolver** | 新建 `expert-work-common.credentials.CredentialsResolver`:`resolve_provider(tenant_id, provider) → secret_ref` / `resolve_tool(tenant_id, tool) → secret_ref`;mode 切换走这个 resolver | Mini-ADR O-3 |
| **O.4 All-or-nothing 校验** | tenant_config PATCH 时:`credentials_mode="tenant"` 必须包含该租户**已用 provider**的全部凭证 + 已用 tool 的全部凭证;agent manifest publish 时:provider 必须 ∈ supported_providers(双重 gate) | Mini-ADR O-4 |
| **O.5 Legacy 迁移** | settings 现有 `embedding_api_key_ref` / `rerank_api_key_ref` / `tavily_api_key_ref` 改派生自 platform catalog(legacy 字段保留 1 个版本作 fallback,加 deprecation warning) | Mini-ADR O-5 |
| **O.6 Caller 集成** | embedder(`resolve_embedder`)+ consolidator aux model(Sprint #7 wire 同步完成)都走 CredentialsResolver;reranker / tavily 留 PR 2 | Mini-ADR O-6 |
| **O.7 Admin UI** | Settings 加 "Credentials" 面板:展示当前 mode + 凭证列表(provider × secret_ref) + mode 切换按钮(带 dry-run 校验)+ 凭证 CRUD | Mini-ADR O-7 |
| **O.8 Audit + 可观测** | 4 audit actions:`CREDENTIALS_MODE_CHANGED` / `PROVIDER_CREDENTIALS_UPDATED` / `TOOL_CREDENTIALS_UPDATED` / `CREDENTIALS_RESOLVE_FAILED` + 5 metrics(by tenant_mode + by role + 401 错误率)+ 2 alerts | Mini-ADR O-8 |
| **O.9 runbook** | 新章节 `docs/runbooks/credentials.md`:平台凭证配置 / 租户 mode 切换流程 / 凭证缺失诊断 | — |

### 1.2 Out-of-scope(明确推迟到 PR 2 / PR 3)

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| reranker / web_search / 其他 tool caller 迁移到 resolver | **PR 2** | 跟 PR 1 同模式,但范围窄(改各 tool 接入点) |
| MCP servers 纳入 mode | **PR 3** | 跟 LLM provider 不同,MCP 有 `name+command+env+secret` 多字段配置,需要独立 ADR |
| **Cost tracking by role**(`expert_work_llm_role_tokens_total`) | M1 评估池 | Sprint #7 已有 `consolidator_llm_tokens`;统一收口在 Stream O 后续 |
| **Per-role rate limit** | M1 | 角色级 rate limit 是平台运营功能,跟 Stream C 已有租户 rate limit 边界 |
| **凭证轮换 / 自动旋转** | M1+ / Stream F.6 | 平台凭证轮换是 SecOps 功能,跟 Stream F SecretStore 边界 |
| Model 升级 / 弃用提醒 UI | M1 | Stream H Admin UI 后续迭代 |

### 1.3 验收(Stream O PR 1 Exit Criteria)

参考 § 4.5 6 条 base + Stream O PR 1 特有 8 条:

- [ ] **migration 0047** backfill 跑通,M0 deployed rows 全部 `credentials_mode='platform'`,legacy `model_credentials_ref` 保持原值(向后兼容)
- [ ] **Platform Catalog 启动校验** — settings 含 `supported_providers` 但某个 provider 缺平台凭证 → 启动 fail fast
- [ ] **CredentialsResolver 双 mode 4 路径** fixture test:platform/provider OK / platform/tool OK / tenant/provider OK / tenant/tool OK + 4 个失败路径(mode 缺凭证)
- [ ] **mode 切换 all-or-nothing** test:`credentials_mode="tenant"` PATCH 但缺 embedding provider 凭证 → 403 + 列出缺哪些
- [ ] **agent manifest publish gate** test:manifest 含一个 `provider="anthropic"` 但平台 `supported_providers` 不含 → publish 403
- [ ] **Caller 集成** — embedder + consolidator aux 经 resolver 拿凭证;运行时 401 fail fast(不静默回退)
- [ ] **4 个新 audit action** protocol + control-plane 两处 Literal 同步 (per [memory:audit-literal-drift])
- [ ] **Admin UI Credentials 面板** Playwright e2e:登录 → 看 mode → 加凭证 → 试切 mode(校验通过)
- [ ] **5 metrics + 2 alerts** 加入,recording rule 单测覆盖
- [ ] **runbook `docs/runbooks/credentials.md`**:平台凭证配置 / 租户 mode 切换 / 401 诊断 / Stream #7 aux wire 步骤
- [ ] CI 全绿(ruff / mypy / pre-commit / CodeQL / pytest / integration / playwright)
- [ ] 单测覆盖 ≥ 80%
- [ ] [memory:ruff-strict-lint-traps] preflight
- [ ] [memory:codeql-unused-global] preflight
- [ ] [memory:alembic-revision-id-32-chars] preflight(0047 命名 ≤ 32 字符)
- [ ] [memory:audit-literal-drift] preflight(grep 两处 Literal)
- [ ] [memory:iteration-plan-sync-after-ship] — ITERATION-PLAN.md Stream O 行同步

---

## 2. 架构

### 2.1 Mini-ADR O-1 — Platform Catalog(env-loaded supported list + credentials)

**决策**:Platform Catalog 由 `settings.py` 配置 + 启动校验。Catalog 包含:

```python
# services/control-plane/src/control_plane/settings.py
class Settings(...):
    # Stream O.1 — Platform Catalog (Mini-ADR O-1).
    # supported_providers 是平台启用的 LLM provider 白名单。租户在
    # tenant mode 下只能给这些 provider 配凭证;agent manifest 引用
    # 不在白名单的 provider 会被 publish 阶段 reject。
    # 默认全集 = ModelSpec.Provider Literal 的所有值;运维可裁剪。
    supported_providers: list[Provider] = Field(
        default_factory=lambda: [
            "anthropic", "openai", "qwen",
            # 其他 provider 默认不启用,需 ops 显式加 env
        ]
    )

    # 每个 enabled provider 必须有对应的平台 secret_ref。
    # 启动期校验:supported - keys(platform_provider_credentials) == empty。
    # 缺凭证的 provider 启动 fail fast。
    platform_provider_credentials: dict[Provider, str] = Field(
        default_factory=dict,
        description="provider → secret:// URI,启动期校验全覆盖 supported_providers"
    )

    # 平台启用的工具白名单。租户在 tenant mode 下只能给这些工具配凭证。
    supported_tools: list[Tool] = Field(
        default_factory=lambda: ["web_search"]
    )

    platform_tool_credentials: dict[Tool, str] = Field(
        default_factory=dict
    )
```

**Provider / Tool 类型定义**(`expert-work-protocol`):

```python
# packages/expert-work-protocol/src/expert_work/protocol/provider_catalog.py(新建)
Provider = Literal[
    "anthropic", "openai", "azure", "self-hosted",
    "kimi", "glm", "deepseek", "qwen", "doubao",
]

# Stream O 仅覆盖外部 SaaS API 工具;内置工具(filesystem / exec_python)
# 走沙箱权限模型,不需要凭证。MCP servers 见 PR 3。
Tool = Literal[
    "web_search",     # Tavily / Serper / etc
    # 后续扩展(image_gen / code_interp / 其他外部 SaaS tool)
]

# 全集合 = 类型系统知道的所有 provider。settings.supported_providers
# 只能是这个的子集。enabled = settings 选择,supported = catalog 上限。
PROVIDER_CATALOG: tuple[Provider, ...] = (
    "anthropic", "openai", "azure", "self-hosted",
    "kimi", "glm", "deepseek", "qwen", "doubao",
)

TOOL_CATALOG: tuple[Tool, ...] = ("web_search",)
```

**启动校验**(`control_plane.app.create_app`):

```python
# 1. supported_providers ⊆ PROVIDER_CATALOG (静态,Pydantic 自动)
# 2. set(supported_providers) == set(platform_provider_credentials.keys())
#    → 缺凭证 OR 多余凭证 → fail fast with explicit list
# 3. supported_tools 同样校验
```

**为什么 startup fail-fast 不是 lazy 检查**:credentials 缺失等到运行期暴露 = 已经有租户在用,影响面大 + 排错难。启动期 fail = 部署 pipeline 那一刻就崩,运维立即知道。

---

### 2.2 Mini-ADR O-2 — tenant_config schema 扩展

**决策**:`TenantConfigRecord` 加 1 个 mode 字段 + 重组 2 个凭证字段。

```python
# packages/expert-work-protocol/src/expert_work/protocol/tenant_config.py
CredentialsMode = Literal["platform", "tenant"]

class TenantConfigRecord(BaseModel):
    ...
    # Stream O — Mini-ADR O-2. 租户凭证模式。
    # "platform": 用平台 catalog 凭证,租户的 provider/tool credentials
    #              字段被忽略(但保留,不删除 — 允许租户先配后切)。
    # "tenant":   用租户自配凭证,平台 catalog 凭证不参与解析。
    # 切换走 TenantConfigService.upsert_credentials_mode 专用 API,
    # 校验 all-or-nothing 完整性(Mini-ADR O-4)。
    credentials_mode: CredentialsMode = "platform"

    # provider → KMS secret URI。覆盖范围:
    # - platform mode 下:忽略(但保留,可见可改)
    # - tenant mode 下:必须包含该租户所有"已用 provider"的凭证
    # provider 必须 ∈ Platform Catalog supported_providers(白名单校验)
    provider_credentials: dict[Provider, str] = Field(
        default_factory=dict
    )

    # tool → KMS secret URI。同上规则。
    tool_credentials: dict[Tool, str] = Field(default_factory=dict)
```

**Migration 0047**(命名 `0047_tenant_credentials` = 23 字符 ✓):

- 加 `credentials_mode TEXT NOT NULL DEFAULT 'platform' CHECK (credentials_mode IN ('platform','tenant'))`
- **rename** `model_credentials_ref` → `provider_credentials`(in-place rename,数据保留)
- 加 `tool_credentials JSONB NOT NULL DEFAULT '{}'::jsonb`
- 加 CHECK:`jsonb_typeof(provider_credentials) = 'object'`
- 加 CHECK:`jsonb_typeof(tool_credentials) = 'object'`

**为什么 rename 而非新加字段**:`model_credentials_ref` 是 Stream C.7 遗留字段,语义不准确(都叫"model credentials"但只是 provider key)。rename + JSONB shape 不变 = 数据保留 + 命名清晰。Risk:其他 caller 引用 `model_credentials_ref` 字段名;PR 1 必须 grep 改全。

**为什么 `provider_credentials` 默认 `{}` 而非 NULL**:跟 `tool_credentials` 一致 + 简化 resolver 路径(永远是 dict,no None check)。

---

### 2.3 Mini-ADR O-3 — `CredentialsResolver`(expert-work-common 单一来源)

**决策**:新建 `expert-work-common.credentials.CredentialsResolver`,统一解析所有 provider/tool 凭证。所有 caller(LLMRouter / Embedder / Reranker / web_search tool / consolidator aux)走这个 resolver,不再自己读 settings。

```python
# packages/expert-work-common/src/expert_work/common/credentials/resolver.py(新建)
class CredentialsResolverError(Exception):
    """Raised when a credential lookup fails (tenant mode + missing cred)."""

class CredentialsResolver:
    """Single source of truth for "which secret_ref do I use for X?"

    Backed by:
    - Platform Catalog (settings) — read once at startup
    - TenantConfigService — read per tenant lookup, cached via existing TTL

    Returns a ``secret://`` or ``kms://`` URI; the SecretStore (Stream F.6)
    resolves the URI to the actual value at LLM-call time.
    """

    def __init__(
        self,
        *,
        platform_provider_credentials: dict[Provider, str],
        platform_tool_credentials: dict[Tool, str],
        tenant_config_service: TenantConfigService,
    ) -> None:
        self._platform_providers = platform_provider_credentials
        self._platform_tools = platform_tool_credentials
        self._tenant_config = tenant_config_service

    async def resolve_provider(
        self, *, tenant_id: UUID, provider: Provider,
    ) -> str:
        """Returns the secret_ref to use for this (tenant, provider) pair.

        Raises CredentialsResolverError when tenant mode + missing creds.
        """
        cfg = await self._tenant_config.get(tenant_id=tenant_id)
        if cfg.credentials_mode == "platform":
            secret_ref = self._platform_providers.get(provider)
            if secret_ref is None:
                msg = f"platform credentials missing for provider={provider}"
                raise CredentialsResolverError(msg)
            return secret_ref
        # tenant mode
        secret_ref = cfg.provider_credentials.get(provider)
        if secret_ref is None:
            msg = (
                f"tenant {tenant_id} in 'tenant' mode but no credentials "
                f"configured for provider={provider}. Switch to 'platform' "
                f"mode or add credentials via PUT /v1/tenants/{tenant_id}/config."
            )
            raise CredentialsResolverError(msg)
        return secret_ref

    async def resolve_tool(
        self, *, tenant_id: UUID, tool: Tool,
    ) -> str:
        """Same shape as resolve_provider but for tool API keys."""
        cfg = await self._tenant_config.get(tenant_id=tenant_id)
        if cfg.credentials_mode == "platform":
            secret_ref = self._platform_tools.get(tool)
            if secret_ref is None:
                msg = f"platform credentials missing for tool={tool}"
                raise CredentialsResolverError(msg)
            return secret_ref
        secret_ref = cfg.tool_credentials.get(tool)
        if secret_ref is None:
            msg = (
                f"tenant {tenant_id} in 'tenant' mode but no credentials "
                f"configured for tool={tool}."
            )
            raise CredentialsResolverError(msg)
        return secret_ref
```

**为什么放 expert-work-common 而非 control-plane**:
- orchestrator(`LLMRouter`)也要调用(agent 主路径)— 跨服务共享
- 跟 `SecretStore`(F.6,expert-work-common)同款抽象层级 — credentials resolution + secret resolution 是 platform 通用能力

**为什么 caller 触发 401 fail fast 不静默回退**:[memory:business-value-over-implementation-cost] —— 静默回退会导致租户以为在用自己 key 但实际用平台 key,billing + 合规面都错。fail fast = 透明信号。

**缓存策略**:`TenantConfigService.get` 已经有 TTL 缓存(默认 30s);resolver 不加额外缓存。30s 内 mode 切换有最大 30s 延迟生效 — 接受。

---

### 2.4 Mini-ADR O-4 — All-or-nothing 校验(2 个 gate)

**决策**:`credentials_mode` 切换 + agent manifest publish **两处都 gate**。

**Gate 1 — `TenantConfigService.upsert` 凭证完整性校验**:

```python
async def upsert(self, *, tenant_id, patch, actor_id) -> TenantConfigRecord:
    # 现有 upsert 逻辑 ...

    # Stream O Mini-ADR O-4 — 切换到 tenant mode 时校验凭证完整性
    if (
        patch.credentials_mode == "tenant"
        and (existing is None or existing.credentials_mode != "tenant")
    ):
        # 计算该租户"已用 provider 集合"+ "已用 tool 集合"
        used_providers = await self._list_used_providers(tenant_id)
        used_tools = await self._list_used_tools(tenant_id)

        # patch 含的凭证 + 已有 record 的凭证 = 合并后必须覆盖 used
        merged_providers = set(
            (patch.provider_credentials or existing.provider_credentials or {})
        )
        merged_tools = set(
            (patch.tool_credentials or existing.tool_credentials or {})
        )

        missing_p = used_providers - merged_providers
        missing_t = used_tools - merged_tools
        if missing_p or missing_t:
            raise CredentialsModeSwitchIncompleteError(
                missing_providers=sorted(missing_p),
                missing_tools=sorted(missing_t),
            )

    # 现有 commit ...
```

`_list_used_providers(tenant_id)` 实现:
- 遍历该租户所有 active agent manifest(`AgentSpecStore.list_for_tenant`)
- 收集所有 `spec.model.provider` + `spec.vision.model.provider` + 所有 sub-agent 同款 + `policies.memory_consolidation.aux_model.provider`(Sprint #7)
- 收集 `tenant_config.mcp_servers` 中声明的 provider(MCP PR 3 时补)

`_list_used_tools(tenant_id)`:
- 遍历 agent manifest 的 `spec.tools` 字段(web_search 等外部 SaaS tool)

**Gate 2 — agent manifest publish 时 provider 白名单校验**:

```python
# control_plane/api/agents.py 现有 POST /v1/agents (publish manifest)
async def publish_manifest(spec: AgentSpec, ...):
    # 现有校验 ...

    # Stream O Mini-ADR O-4 — provider 白名单
    used = collect_providers_from_spec(spec)
    invalid = used - set(settings.supported_providers)
    if invalid:
        raise ManifestProviderNotSupportedError(invalid)
```

**Error 类**(放 `control_plane.tenancy`):

```python
class CredentialsModeSwitchIncompleteError(ValueError):
    """Raised when switching to tenant mode but credentials don't cover
    all currently-used providers / tools."""

    def __init__(
        self, *,
        missing_providers: list[Provider],
        missing_tools: list[Tool],
    ) -> None:
        super().__init__(
            f"cannot switch to tenant mode: missing credentials for "
            f"providers={missing_providers} tools={missing_tools}"
        )
        self.missing_providers = missing_providers
        self.missing_tools = missing_tools

class ManifestProviderNotSupportedError(ValueError):
    """Raised when manifest references a provider not in
    settings.supported_providers."""
```

**API 错误格式**(403):

```json
{
  "error": "credentials_mode_switch_incomplete",
  "message": "cannot switch to tenant mode: missing credentials for ...",
  "missing_providers": ["anthropic", "qwen"],
  "missing_tools": ["web_search"]
}
```

**为什么不在切换时强制租户填齐**:租户可能先用 "platform" mode 跑一段时间,逐步配自家 key,**最后切**。强制即时配齐 = 多次 PATCH。所以:patch 允许独立配凭证,切换时再校验 union 是否覆盖。

---

### 2.5 Mini-ADR O-5 — Legacy settings 迁移路径

**决策**:现有 `embedding_api_key_ref` / `rerank_api_key_ref` / `tavily_api_key_ref` 改派生自 Platform Catalog,1 个 minor 版本作 fallback + deprecation warning。

**派生规则**:
- `embedding_api_key_ref` → `platform_provider_credentials[<embedding_provider>]`(embedding model 的 provider 推导)
- `rerank_api_key_ref` → 同款
- `tavily_api_key_ref` → `platform_tool_credentials["web_search"]`

**Fallback 策略**(过渡期):

```python
def resolve_legacy_api_key(*, role: str) -> str | None:
    """Stream O 过渡期 fallback。下个 minor 版本(M1 Q?)移除。"""
    # 优先 Platform Catalog
    new_ref = settings.platform_provider_credentials.get(<provider>) \
              or settings.platform_tool_credentials.get(<tool>)
    if new_ref is not None:
        return new_ref
    # Legacy fallback
    legacy = getattr(settings, f"{role}_api_key_ref", None)
    if legacy is not None:
        warnings.warn(
            f"{role}_api_key_ref is deprecated; migrate to "
            f"platform_{provider}_credentials. Removal in M1 Q?.",
            DeprecationWarning,
            stacklevel=2,
        )
        return legacy
    return None
```

**部署 migration 操作**(runbook 写):

1. ops 在 env 加 `EXPERT_WORK_PLATFORM_PROVIDER_CREDENTIALS='{"qwen": "kms://...", "anthropic": "kms://..."}'`(JSON)
2. ops 在 env 加 `EXPERT_WORK_PLATFORM_TOOL_CREDENTIALS='{"web_search": "kms://..."}'`
3. 启动校验通过 → 移除 `EXPERT_WORK_EMBEDDING_API_KEY_REF` 等 legacy env
4. 下个 minor 版本 settings 删 legacy 字段

**为什么不一次性删 legacy**:Stream O PR 1 不强制 ops 同 PR 改 env;留 1 个 minor 版本 fallback = 升级更平滑。

---

### 2.6 Mini-ADR O-6 — PR 1 Caller 集成(embedder + consolidator aux)

**决策**:PR 1 改 2 个 caller 走 resolver:

1. **Embedder**(`control_plane.runtime.resolve_embedder`):
   - 现状:`api_key = await secret_store.get(settings.embedding_api_key_ref)`
   - 改后:`secret_ref = await resolver.resolve_provider(tenant_id=<???>, provider="qwen")` then `api_key = await secret_store.get(secret_ref)`
   - **问题**:embedder 是平台级单例,**没 tenant_id 上下文**!
   - **解决**:embedder 改为按租户 per-call 解析 — `Embedder.embed(texts, *, tenant_id)` 签名扩展。这是 invasive change(影响所有 embedder 调用方:memory writeback, knowledge ingestion, memory recall, DLQ worker)。
   - **PR 1 决策**:embedder 改造**留 PR 2**(范围大),PR 1 只做 consolidator aux + 改 settings 派生(不破契约)
2. **Consolidator aux model**(Sprint #7):
   - 现状:`_NullConsolidatorAuxModel`(no-op)
   - 改后:新建 `LLMRouterAuxModelAdapter` 走 `CredentialsResolver.resolve_provider(tenant_id, provider=<aux_model.provider>)` + LLMRouter
   - **tenant_id 来源**:consolidator worker 已经按 tenant 遍历(SUB-PASS scope),`per-tenant aux model` 解析在 `_consolidate_or_reject` / `_review_lone_item` 时拿到

**PR 1 Caller 总结**:
- ✅ Consolidator aux(Sprint #7 wire 完成,凭证走 resolver)
- ✅ Settings 派生 legacy 字段(透明,不破现有 embedder/reranker/web_search 调用)
- ⏸ Embedder per-tenant 改造 — 推 PR 2(影响面大,独立 Mini-ADR)
- ⏸ Reranker / web_search — 推 PR 2

**PR 1 后能力面**:
- Stream O 数据 + API + Admin UI 全到位
- Consolidator aux LLM 真接通(M1 dogfood 可启动)
- Legacy embedder/reranker/web_search 仍在用 settings legacy 字段(无回归)
- PR 2 把这些逐个迁移,Stream O 完整收口

---

### 2.7 Mini-ADR O-7 — Admin UI 凭证面板

**决策**:Stream H 已有 Settings 页面;Stream O 加 "Credentials" 子 tab。

**3 个 surface**:

1. **mode 切换器**(顶部):
   ```
   Credentials mode: [Platform v]  [Switch to Tenant →]

   ⓘ Platform mode: 所有 LLM/工具调用使用平台凭证。
     租户自配凭证不生效(但保留,可见可改)。
   ```
   点 "Switch to Tenant" 按钮 → POST `/v1/tenants/{id}/config/credentials-mode/dry-run` → 显示 "缺哪些 provider/tool 凭证" → 用户补完 → 真正切换

2. **Provider Credentials 表格**:
   ```
   Provider      | Status (Platform) | Tenant Secret Ref       | Used By
   ─────────────────────────────────────────────────────────────────────
   anthropic     | ✓ Configured       | (not set)                | 3 agents
   openai        | ✓ Configured       | kms://tenant-1/openai    | 1 agent
   qwen          | ✓ Configured       | (not set)                | embedding + 2 agents
   ```
   每行 [Edit] 按钮 → 弹窗输入 KMS URI / 删除

3. **Tool Credentials 表格**(类似):
   ```
   Tool          | Status (Platform) | Tenant Secret Ref       | Used By
   ──────────────────────────────────────────────────────────────────────
   web_search    | ✓ Configured       | kms://tenant-1/tavily    | web_search tool
   ```

**新 testid**(同 Sprint #4/#3 PR C 风格):
- `credentials-mode-current` / `credentials-mode-switch-btn` / `provider-creds-table` / `tool-creds-table` / `provider-creds-edit-btn-{provider}` / `mode-switch-dry-run-result`

**校验提示**:dry-run 返回缺凭证时,UI 红框列出 + "Configure {N} missing credentials" 一键跳转。

---

### 2.8 Mini-ADR O-8 — Audit + 可观测

**新 audit actions**(同时改 protocol + control-plane Literal):

| Action | 触发 |
|--------|------|
| `CREDENTIALS_MODE_CHANGED` | tenant credentials_mode 从 X 切到 Y(audit details 含 from/to) |
| `PROVIDER_CREDENTIALS_UPDATED` | tenant provider_credentials 任一 key 增/改/删 |
| `TOOL_CREDENTIALS_UPDATED` | tenant tool_credentials 任一 key 增/改/删 |
| `CREDENTIALS_RESOLVE_FAILED` | resolver raise(tenant mode 缺凭证)— 401 fail fast 信号 |

**4 个新 action**,protocol + control-plane Literal 双同步(per [memory:audit-literal-drift])。

**Metrics**(`packages/expert-work-common/src/expert_work/common/uplift_metrics.py`):

```python
def record_credentials_mode(*, tenant_id_label: str, mode: str) -> None:
    """Gauge — tenant 当前 mode。label tenant_id_label = 'platform'/'tenant'
    avoids per-tenant cardinality blow-up."""

def record_credentials_resolve(*, role: str, provider: str, mode: str, result: str) -> None:
    """Counter — resolve 调用结果。
    role ∈ provider | tool
    provider ∈ supported_providers + tools
    mode ∈ platform | tenant
    result ∈ ok | missing_cred | unknown_provider"""

def record_credentials_mode_switch_attempt(*, mode_to: str, result: str) -> None:
    """Counter — mode 切换尝试。
    result ∈ ok | incomplete | rejected"""

def record_manifest_provider_rejected(*, provider: str) -> None:
    """Counter — agent manifest publish 被 provider 白名单拒。"""

def record_legacy_credentials_fallback(*, role: str) -> None:
    """Counter — Stream O 过渡期 legacy fallback 触发。
    M1 移除时该计数应已归零。"""
```

**Recording rules**(`tools/observability/rules/uplift.yml` 新 `expert_work_uplift_credentials` group):

```yaml
- record: Expert Work:uplift:credentials_resolve_failure_rate:5m
  expr: sum by (role, provider, mode) (
    rate(expert_work_uplift_credentials_resolve_total{result!="ok"}[5m])
  )

- record: Expert Work:uplift:credentials_tenant_mode_adoption_ratio:1d
  expr: |
    count(expert_work_uplift_credentials_mode == bool 1)  # by tenant_id_label
    / clamp_min(count(expert_work_uplift_credentials_mode), 1)

- record: Expert Work:uplift:legacy_credentials_fallback_rate:1d
  expr: sum by (role) (rate(expert_work_uplift_legacy_credentials_fallback_total[1d]))
```

**Alerts**:

```yaml
- alert: ExpertWorkUpliftCredentialsResolveFailureSpike
  expr: Expert Work:uplift:credentials_resolve_failure_rate:5m > 0.1
  for: 10m
  labels: { severity: P1 }
  annotations:
    summary: "Credentials resolve failure rate > 0.1/s"
    description: "Tenant credentials mode misconfigured or platform cred missing for {{ $labels.provider }}"

- alert: ExpertWorkUpliftLegacyCredentialsFallbackPresent
  expr: Expert Work:uplift:legacy_credentials_fallback_rate:1d > 0
  for: 1d
  labels: { severity: P3 }
  annotations:
    summary: "Stream O legacy fallback still triggering after 1 day"
    description: "Migrate {{ $labels.role }} to platform_provider_credentials and remove legacy env"
```

---

## 2bis. PR 2a — Caller 迁移(embedder / reranker / web_search per-tenant)

> PR B(#324)只接通了 consolidator aux 这一个 caller,并把 embedder / reranker /
> web_search 的 legacy settings 字段打了 deprecation 注释(callers 仍读 legacy)。
> PR 2a 把这三个**平台基础设施 caller** 迁到 `CredentialsResolver`,实现 per-tenant
> 凭证解析,同时保证 legacy 部署零回归。Admin UI 面板拆到 PR 2b。

### 2bis.1 Mini-ADR O-9 — Per-tenant resolving callers

**问题**:embedder / reranker / web_search 当前在 app.py lifespan 里**一次性构造成平台单例**
(api_key 在构造时烧死),没有 tenant_id 上下文。Stream O 要求按租户解析凭证。

**决策**(沿用 O-6 锁定的 `embed(texts, *, tenant_id)` 方向):

| Caller | 协议签名变更 | tenant_id 来源 |
|--------|-------------|----------------|
| Embedder | `Embedder.embed(texts, *, tenant_id: UUID)` | 全部 call site 透传(见下表) |
| Reranker | `Reranker.rerank(*, query, documents, top_k, tenant_id: UUID)` | `KnowledgeRetriever.search` 已有 tenant_id |
| web_search | 不改协议;`WebSearchTool.call` 读 `ctx.tenant_id`(已就位) | `ToolContext.tenant_id` |

**Resolving 包装类**(放 control-plane,结构化实现 orchestrator 协议,**避免 orchestrator 依赖
expert-work-common.credentials**):

- `ResolvingEmbedder(resolver, secret_store, provider, model, base_url)`:
  `embed` 内 `secret_ref = await resolver.resolve_provider(tenant_id, provider)` →
  `api_key = secret_store.get(secret_ref)` → 委托现有 `OpenAICompatibleEmbedder` 逻辑。
  解析在每次 `embed()`(= 每 batch)发生一次,频率低,无需 caching。
- `ResolvingReranker(resolver, secret_store, provider, model)`:解析放在 `LLMReranker`
  **已有的 try/except 内** —— 租户缺 rerank 凭证 → `CredentialsResolverError` 被现有兜底
  捕获 → 退化成 RRF-fused order。**rerank 是可选能力(优雅降级),所以不进 mode-switch gate。**
- `ResolvingTavilyClient(resolver, secret_store)`:`search` 内 `resolve_tool(tenant_id, "web_search")`。
  缺凭证 → raise → ReAct tools 节点包成 `ToolMessage(status="error")`(E-12,本就是 fail-fast)。

**call site tenant_id 透传清单**(全部已验证有 tenant_id 在 scope):

| call site | file | tenant_id 来源 |
|-----------|------|----------------|
| memory recall node | `orchestrator/graph_builder/memory.py` | `configurable_uuid(config, "tenant_id")` |
| memory writeback node | 同上 | 同上 |
| knowledge tool 检索 | `orchestrator/tools/knowledge.py` `search()` | 入参 `tenant_id` |
| knowledge ingestion | `control_plane/knowledge/ingestion.py` | 入参 `tenant_id` |
| semantic chunking | `control_plane/knowledge/chunking.py` | 加 `tenant_id` 入参,由 ingestion 透传 |
| memory CRUD PATCH | `control_plane/api/memory.py` | `_require_caller_user()` |
| DLQ worker 重试 | `control_plane/memory/dlq_worker.py` | `row.tenant_id` |
| consolidator embedder adapter | `control_plane/memory_consolidator.py` | sweep loop 已按 tenant 遍历 |

### 2bis.2 Mini-ADR O-10 — Legacy → effective catalog 派生(零回归)

**问题**:把三个 caller 一律改走 resolver 后,**未 opt-in Stream O 的部署**
(`platform_provider_credentials` 空)会在 platform mode 下 resolve 失败 → embedder 崩。

**决策**(实现 O-5 承诺的 derivation path):`Settings` 加 4 个 `effective_*`
**gap-fill** 计算属性,把 legacy 字段并进 catalog:

```python
embedding_provider: Provider = "qwen"   # 新增:embedder 之前只有 model 名 + 烧死 base_url

@property
def effective_platform_provider_credentials(self) -> dict[Provider, str]:
    merged = dict(self.platform_provider_credentials)         # 显式 Stream O 优先
    if self.embedding_api_key_ref and self.embedding_provider not in merged:
        merged[self.embedding_provider] = self.embedding_api_key_ref
    if self.rerank_api_key_ref and self.rerank_provider not in merged:
        merged[self.rerank_provider] = self.rerank_api_key_ref  # type: ignore[index]
    return merged
# effective_supported_providers = keys(effective_platform_provider_credentials) ∪ supported_providers
# effective_platform_tool_credentials: 同款,tavily_api_key_ref → ["web_search"]
# effective_supported_tools: 同款
```

- **gap-fill 语义**:显式 Stream O 配置永远优先;legacy 只补未显式声明的 provider/tool。
- `_validate_platform_catalog` 仍只校验**显式**字段(不变);derivation 是给 resolver 用的附加视图。
- `CredentialsResolver` 用 `effective_*` 构造 → legacy 部署透明地走 platform mode,**零回归**。
- 触发 derivation 时记 `record_legacy_credentials_fallback(role=...)` —— P3 alert 已在 O-8 就位,M1 移除 legacy 后该计数应归零。

### 2bis.3 Mini-ADR O-11 — Build-gate → runtime-resolution 语义迁移

**现状**:`api_key_ref is None → resolve_embedder 返回 None → 声明 `memory.long_term`
的 agent 在 build 期失败`(平台级 gate)。

**迁移后**:embedder 是 per-tenant resolving,**不能在 build 期判断 THIS 租户是否有 key**。

**决策**:
- **全局可用性 gate 保留**:`embedder = ResolvingEmbedder(...)` iff
  `embedding_provider ∈ effective_supported_providers`,否则 `None`。
  → 完全没配 embedding(legacy + Stream O 都没)的部署,行为不变(build 期失败)。
- **per-tenant 失败移到 runtime**:已 opt-in 的部署,某租户(tenant mode)缺 embedding 凭证 →
  `embed()` raise `CredentialsResolverError`(401-style fail fast,O-3)。这是**多租户的必然**:
  一个租户没配 key 不该让 agent build 全局失败。
- reranker / web_search 的 None-when-unconfigured 同款保留(各自 tool 的 catalog 成员判断)。

### 2bis.4 Mini-ADR O-12 — Mode-switch gate 完整性(infra provider)

**问题**:O-4 的 `_collect_used_providers` 只走 agent manifest 的 model 链(primary +
fallback + vision + consolidation aux),**漏了平台基础设施 provider**:租户切 tenant mode 时,
即使补齐了所有 agent 的 chat provider,仍可能缺 embedding provider → 运行期 memory recall 崩。

**决策**:gate 补 infra provider 判定:
- 任一 agent 声明 `spec.memory.long_term` → `used_providers` 加 `settings.embedding_provider`
  (memory.long_term 是硬能力,缺凭证必崩,必须 gate)。
- rerank **不进 gate**(O-9:缺凭证优雅降级,非硬失败)。
- web_search 已由 `_collect_used_tools` 覆盖(不变)。

`embedding_provider` 经 endpoint 从 `app.state` settings 读入并传给 `_collect_used_providers`。

---

## 2ter. PR 2b — Admin UI Credentials 面板(O-7 落地)

> O-7(§2.7)定了 3 个 surface(mode 切换器 + provider 表 + tool 表)。PR 2a 把后端 caller
> 全迁完后,PR 2b 落地 Admin UI。实现期发现后端缺两个**只读/预览**端点(现有只有
> `GET`/`PUT /config`),补在本 PR;写仍复用 `PUT /config`。

### 2ter.1 Mini-ADR O-13 — 两个新端点 + 面板结构

**问题**:面板要渲染"provider × 平台是否已配 × 租户 secret_ref × 被几个 agent 引用 × 当前 mode",
单靠 `GET /config` 拿不到(catalog 在 settings,used-by 要遍历 agent manifest,平台凭证只暴露存在性)。
切 mode 前还要 dry-run 预览缺哪些凭证(O-7 顶部切换器),而 mode-switch 校验目前只埋在 `PUT /config`(切失败才 403)。

**决策**:加 2 个端点,写复用现有 `PUT /config`。

1. **`GET /v1/tenants/{id}/config/credentials`** — 组合视图(单次请求驱动整个面板):
   ```jsonc
   {
     "mode": "platform",                       // 当前 credentials_mode
     "providers": [
       { "provider": "anthropic",
         "platform_configured": true,          // 平台是否有该 provider 凭证(仅布尔,不回显 secret)
         "tenant_secret_ref": null,            // 租户自配 ref(tenant mode 用;平台 mode 也可见可改)
         "used_by_agents": 3 }                 // 引用该 provider 的 agent 数(含 infra:embedding)
     ],
     "tools": [ { "tool": "web_search", "platform_configured": true,
                  "tenant_secret_ref": "kms://...", "used_by_agents": 1 } ]
   }
   ```
   - `providers` 行集 = `settings.effective_supported_providers`(catalog);`platform_configured` =
     该 provider ∈ `effective_platform_provider_credentials`;`tenant_secret_ref` 来自
     `tenant_config.model_credentials_ref`;`used_by_agents` 复用 O-12 的遍历逻辑(按 provider 计数,
     含 `memory.long_term → embedding_provider`)。
   - tenant admin 可读 catalog(知道平台支持哪些 provider 才能自配),**不回显任何 secret 值**,只回 ref + 布尔。

2. **`POST /v1/tenants/{id}/config/credentials-mode/dry-run`** — 切换前预览:
   ```jsonc
   // req:  { "model_credentials_ref": {...}, "tool_credentials": {...} }   // 拟用的租户凭证(可选,缺则取现有)
   // resp: { "ok": false, "missing_providers": ["openai"], "missing_tools": [] }
   ```
   - 复用 `_collect_used_providers` / `_collect_used_tools` / `_validate_credentials_mode_switch` 的纯逻辑,
     **不落库**。前端"Switch to Tenant"按钮先打 dry-run → 红框列缺项 → 补完 → 真 `PUT`(PUT 仍有 O-4 闸门兜底)。

**写路径**:provider/tool 凭证增改删 + mode 切换,都走现有 `PUT /v1/tenants/{id}/config`(patch
`credentials_mode` / `model_credentials_ref` / `tool_credentials`)—— 不新增写端点。

### 2ter.2 面板结构(`SettingsTenantCredentials.tsx`,挂 `/settings/credentials`)

镜像 `SettingsTenantQuotas`(header + Alert + Antd Table + Modal + `message` toast),用 `--ew-*` token,
zh-CN/en 双语,全交互元素带 `data-testid`,Storybook + Playwright + axe 覆盖。3 个 surface:

1. **mode 切换器**(顶部 Card):当前 mode tag + "Switch to Tenant/Platform" 按钮 → dry-run →
   缺项红框(`Configure N missing` 跳转)→ 确认 PUT。切 platform 无需 dry-run(平台凭证恒在)。
2. **Provider Credentials 表**:`provider | Platform status | Tenant Secret Ref | Used By | [Edit]`;
   Edit 弹窗输入/清空 KMS URI(写 `model_credentials_ref[provider]`)。
3. **Tool Credentials 表**:同构(`tool_credentials[tool]`)。

**cross-tenant(`scope='*'`)**:同其他 Settings 页,显示 block Alert,不允许编辑(per
[[project_stream_n_cross_tenant_admin]] system_admin 只读跨租户)。

---

## 2quater. PR 3 — MCP servers 纳入 credentials_mode

> 2026-05-29 用户初拍:Q1=凭证+allowlist / Q2=拦住。**实现期复议(读连接池代码后)**:
> "按租户解析 MCP secret"要正确做必须**按租户建持久连接 + 连接生命周期/驱逐**,这其实是 M1 的
> per-tenant-pool 架构(代码注释本就标 M1),比初框定的"中偏大"更大更险。**用户改判:降级 ——
> 本次只做 allowlist 强制,per-tenant secret 解析 + 严格闸门 + per-tenant 连接池整体推 M1。**
> (见 [[feedback_design_doc_assumptions_age]]:实现期发现的偏差用 AskUserQuestion 复议,不偷偷改方向。)
>
> **最终交付**:
> - **PR 3a(本次)**= `mcp_allowlist` 强制(纯过滤平台 pool、不新建连接、平台 token)+ 已落地的
>   `mcp_credentials` schema(migration 0048)+ `mcp_auth.resolve_mcp_bearer_ref` —— 后两者**保留为
>   M1 明文基础,本次不接运行时**(forward-reserved,文档标注)。
> - **M1 收 MCP per-tenant**:O-15 的 per-tenant bearer 连接(懒建+缓存+驱逐)+ O-16 的严格闸门 +
>   Credentials 面板 MCP 凭证分类 —— 跟 M1 的 per-tenant pool / 沙箱运行时改造一起做。

### 2quater.1 Mini-ADR O-14 — schema:tenant MCP 凭证 + allowlist 语义

- `tenant_config` 加 `mcp_credentials: dict[str, str]`(server_name → 租户 secret_ref);migration 0048。
  键是平台 MCP server 名(平台定义,非租户输入),值是租户自己的 `secret://` / `kms://` 引用。
- `mcp_allowlist: list[str]` **语义最终定义并强制**(此前是死配置):
  - **空 = 不限制**(放行所有平台 server)—— 向后兼容,现有用 MCP 的 agent 不受影响。
  - **非空 = 白名单**:租户的 agent 只能看到列出的 server 名;其余平台 server 对该租户隐藏。
  - 强制点:agent 组装时按 allowlist 过滤平台 pool(见 O-15)。

### 2quater.2 Mini-ADR O-15 — 按租户 bearer-token 解析(运行时核心)〔**推 M1**〕

> 下述运行时改造(per-tenant 连接 + 懒缓存 + 生命周期)推 M1;`mcp_auth.resolve_mcp_bearer_ref`
> 已在 PR 3a 落地为纯函数,M1 直接复用。

**现状**:`build_mcp_pool` 在启动时一次性建全局 pool,bearer token 在 `_build_mcp_client` 里经
`secret_store.get(config.auth_config["token_ref"])` 解析一次,全租户共享连接。

**改造**(懒 + 按租户缓存,只动 bearer-auth 远程 server;stdio/none 仍共享全局):

1. **MCP 鉴权解析模块**(control-plane 新文件 `mcp_auth.py` —— **文件名避开 "credentials" 子串**,
   harness 禁该路径,见 [[reference_harness_denies_credentials_paths]]):
   `resolve_mcp_bearer_ref(*, tenant_cfg, server_name, platform_token_ref) -> str`:
   - platform mode → 返回 `platform_token_ref`(MCP config 里的 token_ref)。
   - tenant mode → 返回 `tenant_cfg.mcp_credentials[server_name]`,缺 → raise `McpCredentialMissingError`(no fallback,同 O-3)。
   - 不进 expert-work-common 的 `CredentialsResolver`(那个目录 harness 禁改),逻辑独立但 mode 判定一致。
2. **按租户 MCP pool provider**:`make_agent_builder` 注入一个 `mcp_tenant_provider(tenant_id) -> MCPServerPool`:
   - 读 `tenant_config`(mode + mcp_credentials + mcp_allowlist);按 allowlist 过滤平台 server。
   - bearer-auth server:用 `resolve_mcp_bearer_ref` 解析出 ref → `secret_store.get` → 建**该租户的** client;
     按 `(tenant_id, server_name)` **懒建 + 缓存**(只连租户真正用到的;不在启动时 N×M 全连)。
   - stdio / auth_type=none:共享全局 pool 的连接(无租户差异)。
3. **agent build 接入**:`_build(spec)` 从 `spec.metadata.tenant` 取 tenant_id → `mcp_tenant_provider(tenant_id)`
   → `replace(tool_env, mcp_pool=<per-tenant pool>)` → `build_agent`。agent 已按 `(tenant,name,version)` 缓存,天然对齐。

### 2quater.3 Mini-ADR O-16 — 闸门 + view 端点 + Credentials 面板(MCP 分类)〔**推 M1**〕

> 整节(严格闸门 + view 端点 MCP 字段 + 面板 MCP 分类)依赖 O-15 的 per-tenant secret 运行时,
> 一并推 M1。PR 3a 不动闸门 / view 端点 / 面板。

- **used-by 收集扩展**:`_collect_used_mcp_servers(specs) -> set[str]` —— 遍历 agent manifest 声明 MCP 的、
  且引用到 **bearer-auth 平台 server** 的集合(stdio/none server 无需凭证,不计入)。
- **闸门(O-4 扩展,Q2=拦住)**:切 tenant 模式时,`used_mcp_servers ⊆ keys(merged mcp_credentials)`,
  缺 → 403 `CREDENTIALS_MODE_SWITCH_INCOMPLETE` 的 `missing_mcp_servers` 字段。
- **dry-run 扩展**:resp 加 `missing_mcp_servers`。
- **view 端点扩展**(`GET …/config/credentials`):data 加 `mcp_servers: [{server, transport, requires_credential(bearer?), platform_configured, tenant_secret_ref, used_by_agents, allowed(按 allowlist)}]`。
- **PR 3b Admin UI**:Credentials 面板加第 3 张表(MCP servers,列同 provider/tool + allowed 开关写 `mcp_allowlist`)+ 编辑弹窗写 `mcp_credentials[server]`。

---

## 3. 数据流(综合)

```
Startup:
  Settings 加载 →
  Platform Catalog 校验:
    supported_providers ⊆ PROVIDER_CATALOG
    set(supported) == set(platform_provider_credentials.keys())
  失败 → fail fast,部署崩

Agent manifest publish:
  POST /v1/agents { spec }
  → collect_providers_from_spec(spec)
  → invalid = used - supported_providers
  → 非空 → 403 ManifestProviderNotSupportedError + audit
  → 合法 → 现有 publish 流程

Tenant credentials mode 切换:
  PUT /v1/tenants/{id}/config { credentials_mode: "tenant", ... }
  → TenantConfigService.upsert
  → 校验 (used_providers ∪ used_tools) ⊆ (merged credentials)
  → 不全 → 403 CredentialsModeSwitchIncompleteError + missing 列表
  → 全 → 写 DB + audit CREDENTIALS_MODE_CHANGED + 清缓存 → 下次解析立即生效

LLM call(agent 主路径 / consolidator aux):
  LLMRouter.call(model_spec, tenant_id)
  → CredentialsResolver.resolve_provider(tenant_id, model_spec.provider)
  → mode=platform: 返回 platform secret_ref
  → mode=tenant:   返回 tenant secret_ref(缺 → raise + 401 + audit FAILED)
  → SecretStore.get(secret_ref) → 实际 API key
  → 调 LLM

Web search tool call:
  WebSearchTool.search(query, tenant_id)
  → CredentialsResolver.resolve_tool(tenant_id, "web_search")
  → 同款 mode 决策
```

---

## 4. PR 拆分

| PR | 内容 | 预估 |
|----|------|------|
| **PR A — `stream-o/1-credentials-design`**(本 PR) | 仅本 § 0 ~ § 6 + ITERATION-PLAN backlog;无代码 | 1 天 |
| **PR B — `stream-o/1-credentials-impl`** | migration 0047 + Platform Catalog + tenant_config schema + CredentialsResolver + all-or-nothing 2 gates + consolidator aux LLMRouter adapter + Admin UI Credentials 面板 + 4 audit actions + 5 metrics + 2 alerts + runbook + 单测 + 集成测 + e2e + Legacy settings 派生 | ~2 周 |
| **PR 2a — `stream-o/2a-caller-migration`**(本 PR) | embedder / reranker / web_search 迁到 resolver(per-tenant);O-9 resolving 包装类 + O-10 legacy effective-catalog 派生 + O-11 build→runtime gate 迁移 + O-12 mode-switch gate 补 embedding_provider;协议签名变更 + 全 call site tenant_id 透传;单测 + 集成测 | ~3-4 天 |
| **PR 2b(Stream O 后续)** | Admin UI Credentials 面板(mode 切换器 + dry-run + provider/tool 凭证表)— O-7 设计落地 | ~3-4 天 |
| **PR 3(Stream O 后续)** | MCP servers 纳入 mode + 现有 mcp_servers 字段 schema 迁移 + MCP-specific Admin UI | ~1 周 |

---

## 5. Stream O PR 1 与其他 Stream 的依赖

- **依赖**:
  - Stream F.6 `SecretStore`(已 M0)— resolver 返回 secret_ref,SecretStore 解析为实际值
  - Stream C.7 `TenantConfigService`(已 M0)— credentials 跟其他 tenant_config 字段同款 CRUD 接口
  - Capability Uplift Sprint #7 `MemoryConsolidator`(已 M0)— consolidator aux wire 是 PR 1 第一个真实 caller
  - Stream H Admin UI(已 M0 + H.4)— Credentials 面板挂 Settings 子路由

- **被依赖**:
  - **#7 凝结引擎真启动**:PR B merge 后,consolidator aux 从 no-op 切到真 LLM,M1 dogfood 才能采到真实凝结数据
  - **M1 高合规客户**:tenant mode 通道开通后,enterprise 客户能用自家 LLM key 跑全部 agent + 工具
  - **Stream M(M0→M1 Gate)**:gate exit criteria 含 "高合规 dogfood" 场景,Stream O 是该场景的能力前置

---

## 6. 关键决策点(2026-05-28 用户拍板,实施期不可推翻)

| # | 决策 | 选 | 反对方案为何弃 |
|---|------|----|-----------------|
| 1 | 模型名锁的颗粒度 | 平台锁 provider,model 名 tenant/agent 可选 | 锁 model 名清单 → 任何新模型要管理员审批,运维负担太重 |
| 2 | 切换 mode 的校验时机 | 切换 API 时强制校验完整性,缺凭证 403 | 允许立即切运行期 401 → 运行期才暴露,租户已跑 agent 突然挂 |
| 3 | mode 切换对运行中 agent 的影响 | 立即生效(resolver 每次解析,无 caching) | 仅新 session / 完全重建 cache → 跟现有 tenant_config 模型不一致 |
| 4 | MCP servers 纳入本 Stream | 推后到 PR 3(本 Stream 只覆盖 LLM provider + tool API key) | 同 Stream 收口 → MCP 配置复杂,独立 PR 更聚焦 |

---
