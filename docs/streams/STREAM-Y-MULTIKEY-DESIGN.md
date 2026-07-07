# Stream Y — Per-Provider Multi-Key Failover (Y-MK)

平台为每个 LLM provider 配置**多把 key**,运行时按优先级在 key 间故障切换;一个
provider 的所有 key 都不可用时才切到下一个 provider。解决"单 key 被限流 / 欠费 /
撤销即整条 run 挂掉"的可用性缺口。

- 状态: 已交付(L1–L6b 全完成);仅计量 `provider_key_id` label 作二期可选(L6c)
- 范围: **平台级**(`platform_provider_secret`)。租户级 override 多 key、加权负载均衡 = 二期 (Y-MK-2)。
- 关联: Stream E.11 (LLMRouter fallback) / Stream P (platform secrets) / Stream Y-1/Y-2 (BYOK 移除、平台凭证唯一源)。

## 1. 背景与根因

### 1.1 现状是硬约束 1:1
- `platform_provider_secret` 主键 = `provider`(migration 0049),结构上同 provider 只能存一条 secret_ref。
- `PlatformSecretsService.effective_provider_credentials()` 返回 `dict[Provider, str]`(`platform_secrets.py:63`)。
- `build_llm_router` 每个 model entry 经 `provider_key_resolver(entry.provider)` 只拿一条 ref(`agent_factory.py:1354`)。
- LLMRouter 的 fallback 链(E.11)只跨**不同 provider/model**(`_flatten_chain`),从无同 provider 多 key。

### 1.2 router/breaker 早为多 key 而生(关键复用点)
`ProviderHandle.key` 注释(`router.py:112-124`)明示:breaker 按 upstream key 隔离,
"one tenant can hold multiple keys for the same vendor and they must fail in isolation"。
即 **per-key circuit breaker + per-handle rate-limit + 链式 fallback 主体已就绪**,
缺的只是"把多 key 喂进链"和"两级跳转语义"。

### 1.3 错误映射把"账号死"误判成"请求坏"
provider 适配器现状(`openai.py:174-179` / `anthropic.py:189-194`):

| HTTP | 现归类 | 现行为 | 缺陷 |
|---|---|---|---|
| 429 | `LLMRateLimitError` | retry 3x → 切下个 provider | 不先试兄弟 key |
| 402 (deepseek 欠费) | `LLMClientError` | 立即死,不 retry 不 fallback | 欠费被当请求坏 → run 挂 |
| 403 / 429-`insufficient_quota` (openai 欠费) | Client / RateLimit | 死 或 白 retry | 账号死被误判 |
| 401 (key 撤销) | `LLMUnauthorizedError` | non-OAuth re-raise,死 | key 坏不试兄弟 |
| 400 (malformed) | `LLMClientError` | 立即死 | 期望: 换 provider |

根因: 现仅"4xx 全停 / 5xx+429 瞬时"两类,缺**"key/账号坏"轴**。

## 2. 目标语义(用户拍板)

1. 某 key 限流 / 欠费 / 撤销 → 跳**同 provider 的下一把 key**。
2. 某 provider 的**所有 key**都不可用 → 跳**下一个 provider**。
3. provider 的**请求本身报错**(400 malformed / 5xx / 网络 / 超时)→ 直接跳**下一个 provider**,跳过本 provider 剩余兄弟 key(同一把坏请求换 key 也白搭)。
4. 不做加权负载均衡,只做**优先级有序 failover**(Y-MK-1)。
5. 平台级先行;租户级 override 仍是单 key(HX-8 不变),作为 1 元列表参与。

## 3. 设计(6 层,blast radius 最小化)

### 3.1 错误分类(expert-work-runtime)
`llm_error_handling.py` 新增:
```python
class LLMKeyUnavailableError(LLMError):
    """此 key/账号当前不可用(欠费/quota 耗尽/撤销)→ router 跳兄弟 key。
    不 retry(backoff 纯浪费),记 breaker(账号死,本进程后续跳过)。"""
```
**key 级错误**(router 跳兄弟 key):`LLMRateLimitError`(限流,middleware 仍 backoff retry)
+ `LLMKeyUnavailableError`(不 retry)+ non-OAuth `LLMUnauthorizedError`。
**请求/provider 级错误**(router 跳下个 provider,跳过兄弟 key):`LLMClientError`(400)
+ `LLMServerError`/5xx/network/timeout/`CircuitOpenError`。

middleware 行为: `LLMKeyUnavailableError` 与 `LLMClientError` 一样**不进 retry 循环**,
但**记 breaker.record_failure()**(账号死 → 开熔断,30s 内本进程跳过该 key)。

### 3.2 provider 适配器 status 映射细化(orchestrator/llm/providers)
`openai.py` / `anthropic.py` / `openai_compatible.py`:
- `402` → `LLMKeyUnavailableError`(deepseek "Insufficient Balance")。
- `403` → 读 body,billing/quota 类 → `LLMKeyUnavailableError`,否则 `LLMClientError`。
- `429` → 读 body,`insufficient_quota` / `billing` → `LLMKeyUnavailableError`,纯限流 → `LLMRateLimitError`。
- `401` → `LLMUnauthorizedError`(保持,OAuth 刷新路径不变;non-OAuth router 端按 key 级处理)。

body 判定用小工具 `_classify_4xx(status, body) -> type[LLMError]`,单测覆盖各家文案。

### 3.3 LLMRouter 两级链(不改 `providers` 签名)
`ProviderHandle` 加字段:
```python
@dataclass(frozen=True)
class ProviderHandle:
    provider: LLMProvider
    key: str            # "<provider>:<model>#<key_id>" — breaker key
    group: str = ""     # "<provider>:<model>" — 兄弟 key 共享;默认空 → 回退取 key(单例,向后兼容)
```
`__call__` 改 group-aware 游走(扁平 list 不变;老的单 key 调用方每 handle 自成 group,行为零变):
```
i = 0
while i < len(handles):
    try: return await _call_one(handles[i])
    except 〈key 级〉 as e: last = e; i += 1; continue        # 下一 handle(兄弟优先,兄弟尽自然到下 provider)
    except LLMError as e:                                     # 请求/provider 级
        last = e
        g = group_of(handles[i])
        while i < len(handles) and group_of(handles[i]) == g: i += 1   # 跳过本 group 剩余兄弟
全程未返回 → AllProvidersExhaustedError(last)
```
`group_of(h) = h.group or h.key`。`_call_one` 的 OAuth 刷新(L8)保留;non-OAuth
`LLMUnauthorizedError` 归入 key 级。`_handle_unauthorized` 刷新失败抛的 `LLMAuthError`
(LLMServerError 子类)= provider 级 → 跳下个 provider(保持现语义)。

### 3.4 存储 1:1 → 1:N(expert-work-persistence)
迁移 `0084_provider_secret_multikey`(down_revision `0083_platform_billing_config`):
- `platform_provider_secret`: 加列 `key_id TEXT NOT NULL DEFAULT 'default'`、`priority INT NOT NULL DEFAULT 100`。
- 主键 `(provider)` → `(provider, key_id)`。
- 存量行 `key_id='default'` 平滑迁移(in-place,backfill default)。
- `tenant_provider_secret` **不动**(租户多 key = 二期)。

ORM `PlatformProviderSecretRow` + 协议 `PlatformProviderSecretRecord` 加 `key_id` / `priority`。
`PlatformSecretStore`:`get_provider`/`upsert_provider`/`delete_provider` 加 `key_id` 维度;
新增 `list_provider_keys(provider) -> list[...]`(按 priority, key_id 排序)。memory + sql 双实现。

### 3.5 解析 + 装配(control-plane + orchestrator)
- `PlatformSecretsService`: 新增 `effective_provider_secret_refs() -> dict[Provider, list[str]]`
  (env seed 作 1 元列表;DB enabled 行按 priority 聚合;disabled 行剔除;全 disabled → 该 provider 不出现)。
  保留旧 `effective_provider_credentials()`(取每 provider 列表首项)以兼容非 router 调用方(embed/rerank)。
- expert-work-common `CredentialsResolver` **禁改**(harness)。照 HX-8 先例,在 control-plane 加
  **plural resolver**:`ProviderKeysResolver = Callable[[str], Awaitable[list[str]]]`,
  读新 service 视图;租户有单 override → 返该 ref 的 1 元列表(HX-8 语义保留),否则返平台列表。
- `agent_factory.build_llm_router`:`provider_key_resolver` 升级为返回 list;每个 model entry
  把 list 展开成多个 `ProviderHandle`,`key=f"{provider}:{name}#{key_id}"`,`group=f"{provider}:{name}"`。
  旧单值 resolver 调用方用适配器包成 1 元列表,避免广播式破坏。

### 3.6 计量 + admin-ui
- 计量: `token_usage` 加 `provider_key_id`(多 key = 多 vendor 账号时按 key 对账各家发票;
  chargeback 按 agent **不变**)。rollup 增 per-key spend 视图。【二期可选,Y-MK 主线先打 label】
- admin-ui `SettingsPlatformConfig.tsx`: provider 下挂 key 列表行,显示 key_id / priority /
  enabled / 健康态(breaker open / 最近 429·402 时间)/ per-key spend;CRUD 加 key_id。

## 4. 关键决策与理由

- **不改 router `providers` 签名,改加 `group` 字段**:扁平 list + group 跳过,向后兼容所有现有
  单 key 调用方与测试,blast radius 最小。
- **欠费 → breaker 开**:账号死应在 cooldown 内被跳过,避免每次 run 重撞;30s 后 HALF_OPEN 探活,
  欠费仍在则再开。30s 对"小时级欠费"偏短但安全(每次只浪费一次探活),M1 可调长 cooldown。
- **欠费不 retry**:backoff 对"已欠费"无意义,直接跳兄弟 key 抢时延。
- **请求坏跳 provider 而非全停**:各家校验/上下文上限不同,400 在另一家可能成功;符合用户语义。
  但 400 在所有 provider 都失败时多耗一轮调用 —— 可接受(可用性 > 极限省成本)。
- **expert-work-common 不动**:沿用 HX-8 在 control-plane 包装的成熟先例。

## 5. 测试矩阵(TDD)

| # | 层 | 场景 | 期望 |
|---|---|---|---|
| MK-1 | router | key1 限流(RateLimit)→ key2 成功 | key2 返回,provider 不切 |
| MK-2 | router | key1 欠费(KeyUnavailable)→ key2 成功 | key2 返回 |
| MK-3 | router | provider A 全 key 死 → provider B key1 成功 | B 返回 |
| MK-4 | router | A.key1 请求坏(400)→ 跳过 A.key2,直达 B | A.key2 **未被调用**,B 被调用 |
| MK-5 | router | A.key1 5xx → 跳过 A.key2 直达 B | A.key2 未调用 |
| MK-6 | router | 全 provider 全 key 死 | AllProvidersExhaustedError(last) |
| MK-7 | router | 单 key(group 默认)回归 | 行为同今(#20/#21/#22 不破) |
| MK-8 | middleware | KeyUnavailable 不 retry + 记 breaker | call_next 调 1 次,breaker failure+1 |
| MK-9 | providers | 402 / 429-insufficient_quota / 403-billing 映射 | → KeyUnavailable |
| MK-10 | providers | 纯 429 / 400 / 5xx 映射 | → RateLimit / Client / Server |
| MK-11 | store(mem+sql) | 多 key upsert/list 按 priority 排序 | 有序;主键 (provider,key_id) |
| MK-12 | migration | 0084 升级存量 1 行 → key_id='default' | 平滑,downgrade 还原 |
| MK-13 | service | effective_provider_secret_refs 聚合/排序/disabled 剔除 | 正确 |
| MK-14 | factory | resolver 返多 ref → 展开多 ProviderHandle,group 正确 | handle 数 = key 数,group 同 |
| MK-15 | service | 旧 effective_provider_credentials 取首项兼容 | embed/rerank 不破 |

## 6. 收尾判定(零技术债)
- [x] 无 TODO;测试覆盖 MK-1..15 全绿(unit),sql store + migration 走 integration(本地真 PG 4/4)。
- [x] ruff / mypy(CI 域)全绿;改动文件无 lint。
- [x] 操作端多 key CRUD API + 测试(L6a)。
- [x] uv.lock 无漂移(无依赖变更)。
- [x] admin-ui 多 key 页 + .stories + i18n 双语 + SE-8 接线(L6b)。
- [ ] 计量 `token_usage` 加 `provider_key_id` label(L6c 二期可选)。
- [x] docs/ITERATION-PLAN.md 登记 Y-MK 并勾选;状态置"已交付"。
- [x] CLAUDE 内存: 多 key failover 两级语义 + 错误轴 落档。

## 7. 已交付实现索引(commit)
- L1 错误类 `LLMKeyUnavailableError` — `expert-work-runtime/.../llm_error_handling.py`
- L2 router 两级链 + `ProviderHandle.group` — `orchestrator/llm/router.py`
- L3 status 分类器 — `orchestrator/llm/providers/_errors.py`(openai/anthropic 接入)
- L4 存储 1:N + migration `0084_provider_secret_multikey`
- L5 解析视图 `effective_provider_secret_refs[_for]` + `resolve_provider_keys` + `build_llm_router` 展开
- L6a 操作端 CRUD `PUT/DELETE /v1/platform/credentials/providers/{provider}/keys/{key_id}`
- L6b admin-ui `SettingsPlatformConfig.tsx` 多 key 展开 + 添加密钥(+ SDK/i18n/stories/tests/e2e)
