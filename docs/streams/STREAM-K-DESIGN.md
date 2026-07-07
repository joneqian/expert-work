# Stream K — Capability Hardening Sprint（设计先行）

> 临时 sprint，**先于 Stream J 剩余子项**。落实 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_complete_not_minimal.md) + [memory:no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_no_design_choice_disguise.md)。
>
> **背景**：2026-05-20 用户用"功能可少，能力不可弱"原则审已交付功能（截至 HEAD=fed5640），发现 13 条 (c) 类弱版 —— 已声明 `[x]` 完成的功能在失败模式、可观测、运维路径、正确性某一维上未达生产强度。本 Stream 把这 13 条统一补到生产级，**之后才进** Stream J 剩余子项（J.4 / J.5 / J.7 / J.8 / J.9 / J.10 / J.12 / J.13 / J.15）。
>
> **设计先行规则**（[memory:design-first-iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_design_first_iteration.md)）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条 gap PR 在本文件对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_zero_tech_debt.md)）：本 Stream 收尾必须 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。

---

## 1. 范围 & 边界

### 1.1 In-scope（K1 – K15，对应审计 G1 – G13）

| ID | Gap | 现状 | 本 Stream 交付 | 优先级 | Mini-ADR |
|----|-----|------|---------------|--------|---------|
| **K1** | G1 API Key rotation 缺失 | 有 POST/GET/DELETE，无 `/rotate` | `POST /v1/api_keys/{id}/rotate` + 双活窗口 + audit `api_key:rotate` | P0 Gate 阻塞 | K-1 |
| **K2** | G3 SSE 跨租户隔离 | 实际由 thread 归属校验阻断（误判更正，见 § 1.2） | 补 `test_sse_thread_tenant_isolation` 锁住 invariant + Mini-ADR 写明安全模型 | P0 Gate 阻塞 | K-2 |
| **K3** | G4 retention CI xfail | 两条测试 `permission denied` 跑不起来 | 定位 PG role 交互 → 修测试或修 job → xfail marker 移除 | P0 Gate 阻塞 | — |
| **K4** | G11 LLM cache 伤正确性 | 固定 TTL 1h，无 nocache 入口 | manifest 加 `cache: bool = True` + 中间件 short-circuit + 集成测试 | P0 Gate 阻塞 | K-3 |
| **K5** | G12 gVisor Gate 无 deadline | 推 M0→M1 Gate 但无 Exit Criteria 锁定 | M0→M1 Gate Exit Criteria 显式列入 gVisor 7/7 用例 staging Linux 必跑通条款 | P0 Gate 阻塞 | — |
| **K6** | G2a memory 运维缺口 | 无 forget / edit / list 路径 | control-plane `services/control-plane/src/control_plane/api/memory.py` + `GET/PATCH/DELETE /v1/memory{id}` + 迁移加 `deleted_at` 列 + RLS 测试 | P1 Stream J 收口前 | K-4 |
| **K7** | G2b writeback 无重试无 dedup | 失败 log + 吞；重复 run 产生重复 | 迁移加 `content_hash` 列 + `UNIQUE (tenant_id, user_id, content_hash)` + writeback 失败入 DLQ table（最简：`memory_writeback_dlq`）+ 后台 retry worker | P1 Stream J 收口前 | K-5 |
| **K8** | G8 J.1 `update_plan` 工具缺失 | planner 只跑一次，agent 无重规划路径 | 把 reflect.revise 的 `revised_steps` 暴露为 agent 可调用 `update_plan` 工具 | P1 Stream J 收口前 | — |
| **K9** | G9 reflect 无 wall-clock 超时 | budget 只算次数，LLM hang 锁死 run | `reflect_node` 套 `asyncio.wait_for(deadline)`，超时降级 `accept` + 集成测试 | P1 Stream J 收口前 | — |
| **K10** | G10 G.7 大盘"No data" | 3 条 SLO 指标 M0 未 emit | orchestrator/sandbox 补 emit `session_ttft_seconds` / `sandbox_cold_start_seconds` / `durable_resume_seconds` histogram + Prom recording rule + Grafana panel 反指真指标 | P2 Gate 前 | — |
| **K11** | G13 加权金丝雀 | 只有蓝绿，无加权流量 | nginx upstream `weight=` + `deploy.py --canary 10/30/50/100` 渐进推进循环 + 失败自动回滚 + 集成测试 | P2 Gate 前 | — |
| **K12** | G2c 中英文 embedding eval gate | 无召回质量基线 | `tools/eval/sets/memory_recall_zh_en/` benchmark set + 基线写入 SLO + CI 跑分 | P3 M1 入口前 | — |
| **K13** | G5 KMS 轮换演练 | 有实装，无轮换 + 缓存失效测试 | fake KMS endpoint 切换 + cache invalidate + 集成测试"轮换后 60s 内取新值" | P3 M1 入口前 | — |
| **K14** | G6 WORM 恢复演练 | 备份脚本有，无恢复 runbook | `docs/runbooks/audit-restore.md` + 恢复脚本 + 集成测试"假装 audit_log 损坏从 WORM 恢复" | P3 M1 入口前 | — |
| **K15** | G7 PG 恢复演练 | 备份脚本未完整定位，无 RTO/RPO 数据 | 定位备份现状 → `docs/runbooks/pg-restore.md` + 演练脚本 + RTO/RPO 实测数据写入 SLO | P3 M1 入口前 | — |

### 1.2 误判更正（审计初稿的三处 agent 误判，本 Stream 不补，仅记录在此）

| 误判 | 实际情况 | 证据 |
|------|---------|------|
| ❌ "C.3 API Key 完全缺失" | C.3 已有 POST/GET/DELETE 三端点 + Service Account 关联 | `services/control-plane/src/control_plane/api/api_keys.py:46-148` —— 仅 K1 补 rotation |
| ❌ "F.6 AliyunKms 仍是 NotImplementedError" | F.6 已实装 store + factory + 单测 | `packages/expert-work-runtime/src/expert_work/runtime/secret_store/aliyun_kms.py` —— 仅 K13 补轮换演练 |
| ❌ "J.11 路由规则不生效" | J.11 是**编译期**绑定，`build_step_routers(spec, ...)` 给每节点直接 inject 对应 router | `services/orchestrator/src/orchestrator/agent_factory.py:166-216, 277` —— **不在 Stream K 范围**，已能力达标 |

### 1.3 Out-of-scope（明确推迟，不进本 Stream）

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| Stream J 剩余子项（J.4 / J.5 / J.7 / J.8 / J.9 / J.10 / J.12 / J.13 / J.15） | Stream J 收口 | Stream K 完成后才进 |
| J.6 多模态（进行中） | 已有 PR #167-171 | 本 Stream 不涉 |
| 真 gVisor 7/7 用例运行 | M0→M1 Gate staging Linux | K5 只把它写进 Gate Exit Criteria |
| KMS rotation 在阿里云真环境跑 | M0→M1 Gate | K13 用 fake KMS endpoint 测能力，真环境留 Gate |
| sandbox warm pool | M1-A | 与 K 范围无关 |
| Sub-Agent（J.4）的取消 / 预算下钻 | Stream J | K8 `update_plan` 只覆盖单 agent 重规划 |

### 1.4 验收（Stream K Exit）

1. **K1 – K15 全部 PR 合并**，ITERATION-PLAN § Stream K 全部 `[x]`。
2. **零债 6 条全过**：无 TODO/FIXME/XXX/HACK；unit ≥ 85% / integration ≥ 70% 关键路径；docs 与实现一致；本 Stream 新增组件均 emit metric+log+trace；CI 8/8 + CodeQL 无新增 high/critical；bug 不遗留。
3. **审计原 13 条 gap 在 verification 清单逐条打勾**（见 § 5）。
4. **没有新 gap 进档**：本 Stream 完成时，若发现新 (c) 类弱版，必须当 sprint 内补完或显式移入下一 Stream checklist。
5. Stream J 剩余子项 PR 可开始（解除阻塞）。

---

## 2. 总体架构

### 2.1 Stream K 性质 = 能力强度补全，不是新增能力

不新增子系统、不新建并行架构。每条 gap 都在**现有扩展面**上补：
- **API 层补端点**：K1（API Key rotate）、K6（memory CRUD）
- **现有节点 / 中间件加防御**：K4（cache skip）、K8（update_plan 工具）、K9（reflect 超时）
- **持久层加约束 / 迁移**：K6（deleted_at）、K7（content_hash + UNIQUE + DLQ）
- **测试 / 运维补真路径**：K2（SSE 隔离测试）、K3（CI xfail）、K10（指标 emit + 大盘）、K12 – K15（演练）
- **部署链补能力**：K11（金丝雀加权）
- **Gate 文档补条款**：K5（gVisor）

### 2.2 PR 拆分原则（每条 gap 一 PR）

- 每条 gap 独立 PR，便于 review + 回滚 + 部分上线
- PR 边界守则：`api_keys.py` + `tests/test_api_keys_rotation.py` + migration（如需）—— 不动无关代码（[CLAUDE.md § 3](../../CLAUDE.md)）
- 每 PR 顺序：先 RED（写测试 / 加 xfail 待修）→ GREEN（实装）→ 文档同步（ITERATION-PLAN checkbox + STREAM-K-DESIGN 局部细化补丁如必要）
- Mini-ADR 在本文件 § 4 一次锁定；PR 不另开 ADR 文件，仅引用 K-1 ~ K-5

### 2.3 与现有 Stream / Gate 的关系

| 项 | 阻塞关系 |
|----|---------|
| **K1 / K2 / K3 / K4 / K5** | 阻塞 M0→M1 Gate 入口（P0） |
| **K6 / K7 / K8 / K9** | 阻塞 Stream J 剩余子项（违则违反 [memory:target-product-form](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/project_target_product_form.md) 产品形态承诺） |
| **K10 / K11** | 阻塞 M0→M1 Gate 第一次真生产 release |
| **K12 / K13 / K14 / K15** | 阻塞 M1-A/B/C 入口 |

---

## 3. 各 gap 设计要点

### K1. API Key rotation

**接口**：`POST /v1/api_keys/{api_key_id}/rotate` → `201 { id, prefix, secret, rotated_from }`。
**双活窗口**（Mini-ADR K-1）：rotation 返回**新** secret，旧 secret 保留 `is_active=true` 并标记 `rotated_at`，在 `grace_period_s`（默认 300s，可配）后由 `retention-cleanup-job` 自动失活。窗口内两 key 都可用。
**Audit**：`AuditAction.api_key_rotate`，details 含 `{old_key_id, new_key_id, grace_period_s}`。
**数据模型**：`api_key` 表加 `rotated_at TIMESTAMPTZ NULL` + `rotated_from UUID NULL`（自引用）。迁移 0020。
**验收**：`test_api_key_rotation_double_active_then_old_expires` —— 旧 key 在 grace 内仍能 auth、过 grace 后 401；audit 行落地。
**关键文件**：`services/control-plane/src/control_plane/api/api_keys.py`、`packages/expert-work-persistence/src/expert_work/persistence/auth/api_key.py`、`packages/expert-work-persistence/migrations/versions/0020_api_key_rotation.py`。

### K2. SSE 跨租户隔离（仅补测试 + ADR 锁 invariant）

**安全模型**（Mini-ADR K-2）：SSE 安全 = thread 归属校验。`POST /v1/sessions/{thread_id}/runs` 是**唯一** SSE 入口；`runs.py:191` 用 `threads.get(thread_id, tenant_id=tenant_id)` 强制 thread 归属当前 JWT 租户，跨租户访问返回 404。无 `GET /v1/runs/{run_id}/stream` 这类 reconnect 端点；`run_id` 是 server-generated `uuid4()` 客户端不能伪造；`bridge.subscribe` 是 in-process 无跨服务路径。
**不补 SSE 层 guard**，但补测试锁住这条 invariant，避免未来加 reconnect 端点时漏校验。
**验收**：`tests/test_runs_cross_tenant_sse_rejected` —— tenant B 用合法 token 但传 tenant A 的 thread_id，断言 404，且 SSE stream 不开始。
**关键文件**：`services/control-plane/tests/test_runs_cross_tenant_sse.py`（新建）。

### K3. retention-cleanup-job CI xfail 收尾

**调查路径**：在 CI testcontainers Postgres 上 `DELETE FROM event_log` 报 `permission denied for table event_log`，但本地通过；`audit_log` 同 role/grant 模式正常。怀疑 `expert-work-persistence` 的 `build_rls_sessionmaker` 在 `SET LOCAL ROLE` 时盖掉了 superuser 连接的有效 role。
**修复路径**：
1. 在 CI 上跑两条测试 + `SET log_statement = 'all'` 看实际 SQL；
2. 若是 RLS role 切换问题 → job 用专属 role（`retention_runner`）+ 显式 GRANT DELETE；
3. 若是迁移漏 GRANT → 迁移补 GRANT；
4. 不允许保留 xfail —— 二选一：测试 XPASS（移除 marker）或证伪原假设（重写测试）。
**验收**：`pytest services/retention-cleanup-job/tests/test_job_integration.py::test_event_log_retention_deletes_old_rows -v` + `::test_jwt_blacklist_expired_rows_deleted` 在 CI 上绿；xfail marker 移除。
**关键文件**：`services/retention-cleanup-job/tests/test_job_integration.py:323-396`、`services/retention-cleanup-job/src/...job.py`、`packages/expert-work-persistence/migrations/versions/`（如需补 GRANT）。

### K4. LLM cache 正确性（manifest skip 入口）

**接口**：`AgentSpecBody.cache: CacheSpec | None = None`；`CacheSpec`：
```python
@dataclass(frozen=True)
class CacheSpec:
    enabled: bool = True
    ttl_s: int = 3600
```
**Manifest 用法**：
```yaml
spec:
  cache:
    enabled: false  # 时间敏感 agent 关掉
```
**中间件行为**（Mini-ADR K-3）：`LLMResponseCache` middleware 在 `before_llm_chain` 锚点检查 `MiddlewareContext.spec.cache?.enabled`；`False` 时直接 short-circuit 不查 cache、不 store。**不引入 runtime payload `skip_cache` 入口**（保持 manifest 单一来源）。
**验收**：`test_cache_skipped_when_manifest_disables` —— manifest `cache.enabled=false` 时同样 prompt 跑两次都打到 LLM provider（mock 计数 == 2）。
**关键文件**：`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`、`services/orchestrator/src/orchestrator/middleware/llm_response_cache.py`、`services/orchestrator/tests/test_llm_cache_integration.py`。

### K5. gVisor Gate Exit Criteria

**改动**：`docs/ITERATION-PLAN.md § M0→M1 Gate Exit Criteria` 加一行：
> - [ ] **gVisor 7/7 沙盒安全用例在 staging Linux 全部跑通**（含 `test_gvisor_cve_2019_5736_poc_fails`、`test_gvisor_timing_isolation`）—— 不允许"软推迟"
**及**：`docs/streams/STREAM-F-DESIGN.md § 1.3` 把"用例 6/7 推 Gate 人工"段加上"Gate Exit Criteria 锁定"引用。
**没有代码改动**，纯文档级补条款。
**验收**：grep `gVisor 7/7` 在 ITERATION-PLAN 和 STREAM-F-DESIGN 各匹配 1 行。

### K6. memory CRUD（forget / edit / list）

**接口**：
- `GET /v1/memory?user_id=&kind=&limit=` → 列当前 user 的记忆（分页）
- `PATCH /v1/memory/{id}` → 改 `content` / `kind`；改后强制重新 embed
- `DELETE /v1/memory/{id}` → soft-delete（`deleted_at = now()`）；`MemoryStore.retrieve` 过滤 `deleted_at IS NULL`
**数据模型**（Mini-ADR K-4）：迁移 0021 加 `deleted_at TIMESTAMPTZ NULL`；不加 RLS policy（已有 `(tenant_id, user_id)` 复合隔离 + 应用层校验）；按 `(user_id, deleted_at)` 索引保证 list 性能。
**为什么 soft-delete**：用户可"撤销 forget"；硬删用 retention-cleanup-job 在 `deleted_at + 30d` 后清。
**验收**：`test_memory_crud_per_user_isolation` —— user A 删/改 user B 的记忆 404；retrieve 不返软删项；retention 跑后硬删。
**关键文件**：`services/control-plane/src/control_plane/api/memory.py`（新建）、`services/control-plane/src/control_plane/app.py`（挂路由）、`packages/expert-work-persistence/src/expert_work/persistence/memory.py`、`packages/expert-work-persistence/migrations/versions/0021_memory_soft_delete.py`。

### K7. memory writeback 重试 + dedup

**数据模型**（Mini-ADR K-5）：
- `memory_item` 加 `content_hash CHAR(64) NOT NULL`（SHA-256 hex of normalized content：lowercased + trimmed）；迁移 0022 回填现有行 + 加 `UNIQUE (tenant_id, user_id, content_hash) WHERE deleted_at IS NULL`
- 新表 `memory_writeback_dlq`：`id UUID PK / tenant_id / user_id / thread_id / extracted_json JSONB / attempts INT / next_retry_at TIMESTAMPTZ / last_error TEXT / created_at`
**writeback 节点改动**（`memory.py:172-199`）：
1. 计算 content_hash，`ON CONFLICT (tenant_id, user_id, content_hash) DO NOTHING`（dedup）
2. 写入失败（embed / DB）→ 整批 extracted 入 `memory_writeback_dlq`，`next_retry_at = now() + 60s * 2^attempts`，`attempts < 5`
3. `attempts >= 5` → 留行 + emit metric `memory.writeback_dead_letter`，不再重试（人工 review）
**Retry worker**：复用 `retention-cleanup-job` 进程（M0 单 job 多任务）—— 每 30s 扫 `next_retry_at <= now()` 取 N=10 重试
**验收**：
- `test_memory_writeback_dedup` —— 同 run 跑两次同输入，DB 行数不变
- `test_memory_writeback_db_failure_enters_dlq_and_retries` —— mock DB 第一次失败、第二次成功，DLQ 行 attempts=1 → 最终成功 DLQ 行被清
- `test_memory_writeback_max_attempts_dead_letter` —— 5 次都失败，metric 计数 + DLQ 行保留
**关键文件**：`packages/expert-work-persistence/migrations/versions/0022_memory_dedup_and_dlq.py`、`services/orchestrator/src/orchestrator/graph_builder/memory.py`、`services/retention-cleanup-job/src/...retry_memory_writeback.py`。

### K8. `update_plan` 工具（J.1 闭环）

**接口**：注册一个内置工具：
```python
@tool
async def update_plan(steps: list[PlanStep], reason: str) -> dict:
    """Replace the current plan with revised steps. Use when execution
    diverges from the initial plan."""
```
**行为**：tool call 写回 `AgentState.plan = Plan(steps=steps, revised_reason=reason, revised_at=now())`；reflect.revise 现有 `revised_steps` 处理已支持此 reducer，复用 path。
**激活条件**：仅 `workflow.type == "plan_execute"` 时注册到 tool registry；其他 workflow 不暴露此工具防止误用。
**验收**：`test_update_plan_tool_replaces_state_plan` —— agent 调用 update_plan → 下一步 system context 渲染新 plan；`test_update_plan_unavailable_in_react_mode` —— react workflow 无此工具。
**关键文件**：`services/orchestrator/src/orchestrator/tools/builtin/update_plan.py`（新建）、`services/orchestrator/src/orchestrator/tools/assembly.py`、`services/orchestrator/src/orchestrator/agent_factory.py`。

### K9. reflect wall-clock 超时

**改动**：`reflect_node`（`services/orchestrator/src/orchestrator/graph_builder/reflect.py`）外套 `asyncio.wait_for(_invoke_reflect(...), timeout=deadline_s)`；超时降级为 `Verdict.ACCEPT` + log + emit metric `reflect.timeout`。
**预算 schema**：`ReflectionSpec` 加 `deadline_s: int = 30`（默认 30s 单次 reflect LLM 调用）。
**验收**：`test_reflect_wallclock_timeout_falls_back_to_accept` —— mock LLM hang 31s，reflect_node 返回 accept verdict 且 `reflect.timeout` metric 增 1。
**关键文件**：`services/orchestrator/src/orchestrator/graph_builder/reflect.py`、`packages/expert-work-protocol/src/expert_work/protocol/agent_spec.py`（`ReflectionSpec.deadline_s`）。

### K10. G.7 大盘真闭环

**emit 三个 histogram**（Prom client）：
- `expert_work_session_ttft_seconds`（orchestrator）—— 首 token 延迟，buckets `0.1, 0.25, 0.5, 1, 2, 5, 10`
- `expert_work_sandbox_cold_start_seconds`（sandbox-supervisor）—— 沙盒启动到 ready，buckets `0.05, 0.1, 0.25, 0.5, 1, 2, 5`
- `expert_work_durable_resume_seconds`（control-plane）—— checkpoint 加载到首事件，buckets 同 TTFT
**Recording rule**（`tools/observability/rules/sli.yml` 补 3 条）：每个对应一个 `..._p95_5m` 和 `..._p99_5m`
**Grafana panel**（`tools/observability/dashboards/02-orchestrator.json` / `03-sandbox.json`）：把现有 "No data" panel 的 expr 改成新的 `..._p95_5m`，并删 "Scaffold" 标记
**验收**：
- `pytest services/orchestrator/tests/test_metrics_emission.py::test_session_ttft_histogram_emitted` 红→绿
- `promtool check rules tools/observability/rules/sli.yml` 通过
- 在本地 compose `observability` profile 起来跑 3 个 sample run，大盘 panel 出实数
**关键文件**：`services/orchestrator/src/orchestrator/metrics.py`、`services/sandbox-supervisor/src/sandbox_supervisor/metrics.py`、`services/control-plane/src/control_plane/metrics.py`、`tools/observability/rules/sli.yml`、`tools/observability/dashboards/02-orchestrator.json`、`03-sandbox.json`、`tools/observability/check_metric_names.py`（allowlist 加新指标）

### K11. 加权金丝雀流量

**nginx upstream**：`infra/nginx/conf.d/control-plane.conf` 改 `upstream control_plane { server cp-blue:8080 weight=100; server cp-green:8080 weight=0; }`；`tools/deploy/` 提供 `set_weights(blue, green)` shell helper（`sed` 改 conf + nginx reload）
**deploy.py 改动**：`tools/deploy/deploy.py` 加 `--canary 10,30,50,100` 参数；按列表逐档推进，每档间 `--soak-s`（默认 300s）观察告警；任何告警触发 → 自动回滚到上一档；推完最后档转蓝绿切换
**验收**：
- `test_canary_progressive_traffic_split` —— mock soak 期插入 alert → deploy.py 触发 rollback 调用
- `test_canary_full_succeeds` —— 4 档全过 → 调用蓝绿切换
**关键文件**：`tools/deploy/deploy.py`、`tools/deploy/rollback.py`、`infra/nginx/conf.d/control-plane.conf`、`tools/deploy/tests/test_canary.py`

### K12. memory recall eval gate（中英文）

**Benchmark set**：`tools/eval/sets/memory_recall_zh_en/cases.yaml`（手工策划 50 中 + 50 英）每 case：`memory_corpus`（要预写的 memory_item）+ `query` + `expected_recall_ids`（top-k 应包含的 ids）
**评估指标**：`recall@5`（top-5 中包含目标 id 的比例）+ `mrr@10`（mean reciprocal rank）
**Gate 行为**：CI 跑 `pytest tools/eval/test_memory_recall_set.py`；中英分别要求 `recall@5 >= 0.7`、`mrr@10 >= 0.5`；任一不达标 → 测试 FAIL
**SLO 写入**：`docs/runbooks/slo.md` 加 `memory_recall_quality` 一节，列基线
**关键文件**：`tools/eval/sets/memory_recall_zh_en/cases.yaml`（新建）、`tools/eval/test_memory_recall_set.py`（新建）、`docs/runbooks/slo.md`

### K13. KMS rotation 集成测试

**Fake KMS endpoint**：在 `packages/expert-work-runtime/tests/conftest.py` 加 `fake_aliyun_kms` fixture（启 aiohttp test server 模拟 KMS GetSecretValue，第一次返 v1、被 `rotate()` 调用后返 v2）
**测试**：`test_aliyun_kms_rotation_invalidates_cache_within_ttl`
1. AliyunKmsSecretStore TTL = 60s
2. `await store.get("secret-name")` → "v1"，cache 写入
3. 触发 fake KMS rotation → v2 已是当前
4. `await asyncio.sleep(61)` → cache 过期
5. `await store.get("secret-name")` → "v2"
**配合**：加 `invalidate_now(name)` API 让真生产可强制刷新（不进默认路径）
**关键文件**：`packages/expert-work-runtime/src/expert_work/runtime/secret_store/aliyun_kms.py`、`packages/expert-work-runtime/tests/test_aliyun_kms_secret_store.py`

### K14. WORM 恢复演练

**Runbook**：`docs/runbooks/audit-restore.md` —— 步骤：
1. 确认 audit_log 表损坏症状（行数下降 / 查询失败）
2. 从 S3 Object Lock 桶按时间窗口列对象（`aws s3 ls --recursive`）
3. 用 `tools/persistence/restore_audit.py`（新增）把 ndjson.gz 重放进新建的 `audit_log_restored_<YYYYMMDD>` 表
4. 校验：`SELECT count(*), min(created_at), max(created_at)` 与 WORM 元数据对照
5. 切换：`ALTER TABLE` 重命名或应用层路由（推迟决策点 —— runbook 列两种）
**演练脚本**：`tools/persistence/test_audit_restore_drill.sh` —— testcontainers 启 PG + minio + audit-backup-worker 写出 N 行 → 故意 `TRUNCATE audit_log` → 跑 restore_audit.py → 校验 count 一致
**验收**：CI 跑演练脚本通过；runbook 经人工 review 并入 `docs/runbooks/INDEX`
**关键文件**：`tools/persistence/restore_audit.py`（新建）、`tools/persistence/test_audit_restore_drill.sh`（新建）、`docs/runbooks/audit-restore.md`（新建）

### K15. PG 恢复演练

**先定位现状**：grep `pg_dump`、`pg_basebackup`、`wal-g`、`pgbackrest` —— 确认 M0 当前备份手段（可能 ADR-0002 / subsystems/22 文档化了但未实装）
**Runbook**：`docs/runbooks/pg-restore.md` —— PITR 步骤 + 基础备份 + WAL 重放
**演练脚本**：`tools/persistence/test_pg_restore_drill.sh` —— testcontainers 启 PG → 写 N 行 → 触发备份 → drop database → restore → 校验
**RTO/RPO 实测**：跑 5 次记中位数，写入 `docs/runbooks/slo.md`
**关键文件**：根据现状决定（可能新增 `services/postgres-backup-worker/` 或仅文档化既有 `tools/persistence/`）

---

## 4. Mini-ADR

### Mini-ADR K-1：API Key rotation 用双活窗口，不用即时撤销
即时撤销会让所有正在调用的客户端立刻 401，运维事件转放大。双活窗口（`grace_period_s` 默认 300s）让客户端有时间切换，旧 key 由 `retention-cleanup-job` 在 grace 后自动失活。不允许 `grace_period_s = 0` —— 紧急撤销走 `DELETE /v1/api_keys/{id}`（已有路径），不复用 rotate。

### Mini-ADR K-2：SSE 跨租户安全模型 = thread 归属校验，不加 SSE 层 guard
SSE 仅由 `POST /v1/sessions/{thread_id}/runs` 入口；该路由已在创建时 `threads.get(thread_id, tenant_id=jwt_tenant)` 强制校验 thread 归属当前租户，跨租户 thread_id 返回 404。`run_id` server-generated `uuid4()`，无 `GET /v1/runs/{run_id}/stream` reconnect 端点，`bridge.subscribe` in-process 无跨服务路径。结论：**不加 SSE 层重复 guard**（DRY），但**补 `test_runs_cross_tenant_sse_rejected` 锁住 invariant**。未来若加 reconnect 端点必须重新评估并加 guard。

### Mini-ADR K-3：cache skip 走 manifest，不走 runtime payload
LLM cache 的 skip 入口放 `AgentSpecBody.cache.enabled`（manifest 级），不放 `RunRequest.skip_cache` 或 prompt-level marker。理由：manifest 是 agent 的能力声明面，是否缓存属于 agent 行为定义；运行时 payload 加 skip 会让同一 agent 在不同请求下行为不一致，违反 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-expert-work/memory/feedback_complete_not_minimal.md) "能力清晰"。若未来发现需 per-call skip，再走 manifest 加 per-tool overrides，而非 RunRequest 旁路。

### Mini-ADR K-4：memory 用 soft-delete，不用硬删 + audit
soft-delete（`deleted_at`）允许用户撤销 forget；硬删走 `retention-cleanup-job` 在 `deleted_at + 30d` 后清。不引入 audit 表追踪 forget —— `audit_log` 已记 `DELETE /v1/memory/{id}` 调用足够；memory 本身就是用户可控数据，多一层审计冗余。

### Mini-ADR K-5：memory dedup 用 content_hash 列 + UNIQUE，不用全文本 UNIQUE
全文本 UNIQUE 在 Postgres 上索引 size 不可控；用 `content_hash CHAR(64)`（SHA-256 of normalized content）+ `UNIQUE (tenant_id, user_id, content_hash) WHERE deleted_at IS NULL` 固定索引大小。Normalize 规则：`content.strip().lower()`（M0 简单规则，足以覆盖 95% 重复；M1 可加更复杂的 normalize）。`UNIQUE WHERE deleted_at IS NULL` 让 soft-delete 不阻新 insert 同样内容。

---

## 5. Verification（13 条 gap 闭合清单）

每条 PR 合并时回头打勾：

- [ ] **K1**：`POST /v1/api_keys/{id}/rotate` 返回新 secret + audit `api_key:rotate` 落地 + `test_api_key_rotation_double_active_then_old_expires` 绿
- [ ] **K2**：`test_runs_cross_tenant_sse_rejected` 绿 + Mini-ADR K-2 引用在 `runs.py` docstring
- [ ] **K3**：retention-cleanup-job 两条 xfail → XPASS，marker 移除；ITERATION-PLAN M1-B 挂账行勾掉
- [ ] **K4**：`test_cache_skipped_when_manifest_disables` 绿 + `AgentSpecBody.cache` schema 校验
- [ ] **K5**：grep `gVisor 7/7` 在 ITERATION-PLAN + STREAM-F-DESIGN 各匹配 1 行
- [ ] **K6**：3 条 memory CRUD endpoint 集成测试 + per-user 隔离测试绿
- [ ] **K7**：dedup + DLQ + retry 三条集成测试绿；`memory.writeback_dead_letter` metric emit
- [ ] **K8**：`test_update_plan_tool_replaces_state_plan` + `test_update_plan_unavailable_in_react_mode` 绿
- [ ] **K9**：`test_reflect_wallclock_timeout_falls_back_to_accept` 绿；`reflect.timeout` metric emit
- [ ] **K10**：3 个新 histogram metric emission test 绿；`promtool check rules` 通过；Grafana panel 反指真实 metric（截图入 `docs/runbooks/observability.md` 或 verify by manual smoke）
- [ ] **K11**：两条 canary 集成测试绿；nginx weighted upstream 配置生效
- [ ] **K12**：`test_memory_recall_set` 中英文 recall@5 / mrr@10 达基线
- [ ] **K13**：`test_aliyun_kms_rotation_invalidates_cache_within_ttl` 绿
- [ ] **K14**：`test_audit_restore_drill.sh` CI 通过；`docs/runbooks/audit-restore.md` 合并
- [ ] **K15**：`test_pg_restore_drill.sh` CI 通过；RTO/RPO 实测数据写入 SLO

---

## 6. PR 顺序

按优先级 + 依赖排：

| PR # | Gap | 依赖 |
|------|-----|------|
| 1 | **本文件 + ITERATION-PLAN 插入 Stream K**（设计先行落地） | — |
| 2 | K3 retention CI xfail（CI 调查独立，先动） | PR 1 |
| 3 | K5 gVisor Gate Exit Criteria（纯文档） | PR 1 |
| 4 | K2 SSE 跨租户测试 + Mini-ADR | PR 1 |
| 5 | K4 cache 正确性 | PR 1 |
| 6 | K1 API Key rotation | PR 1 |
| 7 | K9 reflect wall-clock 超时（最小） | PR 1 |
| 8 | K8 update_plan 工具 | PR 1 |
| 9 | K6 memory CRUD（迁移 0021） | PR 1 |
| 10 | K7 memory dedup + DLQ（迁移 0022） | PR 9（迁移序号顺位） |
| 11 | K10 G.7 大盘指标真闭环 | PR 1 |
| 12 | K11 加权金丝雀 | PR 1 |
| 13 | K13 KMS 轮换演练 | PR 1 |
| 14 | K12 memory eval gate | PR 9 + PR 10（依赖 memory 路径稳定） |
| 15 | K14 WORM 恢复演练 | PR 1 |
| 16 | K15 PG 恢复演练 | PR 1 |
| 17 | **Stream K 收尾**：零债 6 条核验 + ITERATION-PLAN 全勾 | PRs 2–16 |

---

## 7. 失败模式（Stream 级）

| 失败 | 触发 | 缓解 |
|------|------|------|
| K3 CI xfail 根因复杂、单 PR 修不动 | PG/asyncpg role 交互超预期 | 拆两步：第一步把测试改用专属 `retention_runner` role + GRANT；第二步若仍失败再深查；不允许保留 xfail |
| K7 迁移在生产 DB 回填 content_hash 时锁表 | 大 memory_item 表 | 迁移用 `ADD COLUMN content_hash CHAR(64) NULL` → 后台 job 分批回填 → 再加 `NOT NULL` + UNIQUE（expand-contract） |
| K10 emit metric 改动散布多服务 | orchestrator / sandbox-supervisor / control-plane 都要改 | 拆三个子 PR（每服务一个），共享 metric naming convention 文件 `tools/observability/metric_names.py` |
| K15 现状备份方案不存在 | M0 备份脚本可能只在文档 | 首步是定位 + 报告；若发现真没实装，把 K15 升级为"实装备份 + 演练"两步走，单 PR 拆两步 |
| Stream K 跨越 7-10 周期间 Stream J.6 多模态新增冲突 | 长 sprint 与 J.6 PR 流并行 | K 优先合并；J.6 在 K 期间 rebase；K8 / K9 / K6 涉及 agent_factory.py 时与 J.6 协调 import |
