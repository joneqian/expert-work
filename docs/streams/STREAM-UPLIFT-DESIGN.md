# Stream Uplift — Hermes-derived Capability Uplift Sprint(设计先行)

> 临时 sprint，**与 M0→M1 Gate 30 天稳定性观察期并行**。落实 [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) + [memory:design-first-iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)。
>
> **任务来源**：`docs/research/helix-vs-hermes-gap.md`(5 条) + Memory 系统讨论新增(3 条)= **8 项 capability uplift**。归档详见 `docs/research/capability-uplift-plan.md`。
>
> **与 Stream L 的关系**：L 补"agent loop 单 turn 内的成熟度"(已 M0 落地)；本 Stream 补"治理 / 持久 agent 必备的运行期安全能力 + Skill / Memory 生命周期"。两者都是 Hermes-derived 但作用面不同。
>
> **零债收尾规则**([memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md))：本 Stream 收尾必须 6 条全过 —— 无 TODO/FIXME / 测试覆盖达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。

---

## 0. 背景与范围澄清

### 0.1 Sprint 提出动机

helix M0 在 [helix-vs-hermes-tldr.md](../research/helix-vs-hermes-tldr.md) 的 15 维度评估中，与 Hermes 对齐的事项均已通过 Stream L 完成；剩下 5 条真 gap + 3 条 Memory 系统的能力短板，决定一次性塞 Gate sprint 而非散到 M1/M2 各 stream，理由：

1. **8 项无相互依赖或仅有软依赖**(详见 `capability-uplift-plan.md` § 8 项总览)，可并行 / 串行混排
2. **Gate 期单人扛得动**(预估 12-13 周单人节奏，可压到 6-8 周双人)
3. **提前做收益大**：Sprint #4 / #7 即使阈值要 M1 调，基础设施先做可早 6-12 个月可用
4. **统一设计文档可避免 8 个 mini-stream 各开各的格式**

### 0.2 8 项总览

| 编号 | 能力 | 实施量 | 真硬依赖 | 设计文档章节 |
|------|------|--------|---------|------------|
| #1 | Cron / Webhook Prompt 注入扫描(含隐形 Unicode) | ~3 天 | 无 | § 2 |
| #2 | Memory 投毒防御 + drift backup | ~1.5 周 | 复用 #1 威胁模式库 | § 3 |
| #3 | Skill 附属文件(references/templates/scripts) | ~2 周 | 无 | § 4 |
| #4 | Curator 自动状态机(active/stale/archived) | ~1 周 | 基础设施可提前；启用调参 M1-K J.7b-1 | § 5 |
| #5 | MCP Server(暴露给 Claude Code/Cursor) | ~2 周 | 无 | § 6 |
| #6 | Memory hybrid retrieval(向量 + 全文 RRF) | ~1.5 周 | 无(port J.5) | § 7 |
| #7 | Memory 短期 → 长期自动凝结 | ~3-4 周 | 凝结引擎可提前；策略调优 M1 dogfood | § 8 |
| #8 | Memory frozen snapshot / 前缀缓存优化 | ~1.5 周 | 无 | § 9 |

### 0.3 Out-of-scope(整个 Sprint 都不做)

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| MCP Server 写权限工具(`messages_send` / `permissions_respond`) | M1-I | M0 仅暴露读权限工具，写跨租户 RLS 风险需先在生产观察一段 |
| Curator 启用阈值调参(30/90 天默认改成什么) | M1 J.7b-1 上线 2-4 周后 | Sprint 内只锁基础设施 + 默认值；启用看真实数据 |
| Memory 凝结策略阈值调优 | M1 dogfood 跑完 | Sprint 内只锁引擎 + 默认 trigger 信号 + 防误学约束 |
| Memory frozen snapshot 设为默认 | M1 后期 | Sprint 内只做 manifest 字段 + per_session 模式，默认仍 per_turn |
| Hermes 内置 22+ 消息平台 adapter | 永不做 | helix 是 backend，末端用户对接是业务系统责任 |
| Hermes 末端用户 CLI(prompt_toolkit) | 永不做 | 同上；helix CLI(M1-I)面向开发者不是末端用户 |
| Hermes 17 hook 系统 / agent 自我注册 tool | 永不做 | helix 走显式 manifest + 审核流程(governance) |

### 0.4 Sprint Exit Criteria

1. **8 项全部 PR 合并**，本文件每个 § 章节 checklist 全部 `[x]`，`docs/ITERATION-PLAN.md` § Capability Uplift Sprint 标 `[x]`
2. **零债 6 条全过**(per [memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md))：无 TODO/FIXME/XXX/HACK；unit ≥ 85% / integration ≥ 70% 关键路径；docs 与实现一致；本 Stream 新增组件均 emit metric + log + trace；CI 全绿 + CodeQL 无新增 high/critical；bug 不遗留
3. **能力指标可量**：每项至少一条 SLO / Prometheus recording rule(每章节末尾详列)
4. **Gate 期 K.K12 eval baseline 不退化**：每项 PR merge 前重跑，任何 baseline 退化 ≥ 5% 卡 PR

### 0.5 PR 命名约定

- branch：`uplift/<id>-<short-name>`，例如 `uplift/1-cron-injection-scan`、`uplift/6-memory-hybrid-rerank`
- commit：`feat(uplift): #<id> — <短描述>`，例如 `feat(uplift): #1 — cron/webhook prompt injection scan with invisible Unicode`
- PR 标题：`feat(uplift): #<id> — <短描述>`

---

## 1. 跨 Sprint 共享基础设施

### 1.1 威胁模式库(被 #1 / #2 共用)

新建模块：`packages/helix-common/src/helix_agent/common/threat_patterns.py`(命名空间 `helix_agent.common`，与 `observability` / `deadline` / `context` 同级)。

**为什么放 helix-common 不放 protocol**：
- protocol 仅放纯数据 contract(BaseModel / Enum / TypedDict)，scan 是行为
- helix-common 已经是"无业务、仅共享工具"的层级，与现有 `observability` / `health` 同性质
- control-plane 和 orchestrator 都需要(control-plane 给 trigger/memory write API 用；orchestrator 给 memory recall / skill load 用)，放 helix-common 避免循环依赖

**模块对外 API**：

```python
# helix_agent.common.threat_patterns
INVISIBLE_CHARS: frozenset[str]       # 17 个隐形 Unicode 字符集

def scan_for_threats(content: str, *, scope: ScanScope) -> list[ThreatFinding]:
    """返回所有命中模式 + 隐形字符。"""

def first_threat_message(content: str, *, scope: ScanScope) -> str | None:
    """便利包装；命中第一条返回错误字符串，否则 None。"""

ScanScope = Literal["all", "context", "strict"]

@dataclass(frozen=True)
class ThreatFinding:
    pattern_id: str             # 命中模式 ID(如 "prompt_injection")
    category: ThreatCategory    # "injection" | "exfil" | "c2" | "invisible_unicode" | "role_hijack" | ...
    severity: Literal["block", "warn"]    # block(strict)/warn(context)
    excerpt: str                # 命中片段(截断 ≤ 200 字符) — 供 audit log 显示
```

**与 Hermes 的差异**：
- helix 加 `ThreatFinding` dataclass(Hermes 返回 `list[str]`)；helix 需要把 finding 完整落 audit 表，光 pattern_id 不够
- helix 加 `severity` 字段，由 scope 决定 — strict → block(创建期返 422)，context → warn(运行期仅 audit)
- helix `excerpt` 字段为后续 SecOps dashboard 准备

**Mini-ADR U-1：威胁模式库放 `helix_agent.common.threat_patterns`**

- **决定**：新增模块在 `packages/helix-common/src/helix_agent/common/threat_patterns.py`
- **替代方案 1**：每个调用方各 copy 一份 → 拒绝(模式 drift + 更新窗口期长)
- **替代方案 2**：放到独立的 `helix-security` 包 → 拒绝(M0 只有 1-2 个调用方，独立包是 over-engineering)
- **替代方案 3**：放到 control-plane 私有模块 → 拒绝(orchestrator 后续要用)
- **更新流程**：威胁模式变更走 PR + 必标记 `security` label；测试矩阵要求新增模式必须配 ≥ 2 个正例 + 2 个反例(避免误杀)

### 1.2 通用 audit action 命名约定(被 #1 / #2 共用)

新增 audit action 加在 `packages/helix-protocol/src/helix_agent/protocol/audit.py`：

```python
class AuditAction(StrEnum):
    # ...
    # capability uplift Sprint(本 Stream)
    TRIGGER_PROMPT_INJECTION_BLOCKED = "trigger:prompt_injection_blocked"   # #1 create/update strict block
    TRIGGER_PROMPT_INJECTION_WARN = "trigger:prompt_injection_warn"         # #1 fire-time context warn
    MEMORY_INJECTION_BLOCKED = "memory:injection_blocked"                   # #2 write strict block
    MEMORY_INJECTION_REDACTED = "memory:injection_redacted"                 # #2 recall 命中替占位符
    MEMORY_DRIFT_DETECTED = "memory:drift_detected"                         # #2 hash 不一致
```

详情字段(`details: dict[str, Any]`)统一约定：

```python
{
  "scope": "strict" | "context",
  "findings": [
    {"pattern_id": "...", "category": "...", "severity": "...", "excerpt": "..."},
    ...
  ],
  "field": "name" | "config.seed_input" | "memory_text" | ...,  # 哪个字段命中
}
```

per [memory:audit-literal-drift](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_audit_literal_drift.md)：`AuditAction` 在 protocol + control-plane 两处 Literal，新增动作 **必须两处同改**。

### 1.3 通用 Prometheus 命名约定

| 命名前缀 | 用途 |
|---------|------|
| `helix_uplift_threat_scan_total{scope, result}` | 任何 scan 调用(#1 / #2 共享)；`result=clean/blocked/warned` |
| `helix_uplift_threat_pattern_hit_total{pattern_id, scope}` | 模式级命中分布(用于 tuning) |

---

## 2. Sprint #1 — Cron / Webhook Prompt 注入扫描

### 2.1 威胁模型

**Attack surface 枚举**(全平台所有进入 agent 的用户可控输入)：

| 入口 | 字段 | 控制者 | 是否当前 attack surface |
|------|------|-------|-------------------------|
| `POST /v1/triggers` create | `body.name` | 管理员 / tenant 用户 | ✅ 进 audit log 显示 + 进 fire 时 emit 字段 |
| `POST /v1/triggers` create | `body.config.seed_input` | 管理员 / tenant 用户 | ✅ **直接成为 HumanMessage 喂给 agent** |
| `PATCH /v1/triggers/{id}` | `body.config.seed_input` | 管理员 / tenant 用户 | ✅ 同上 |
| `POST /v1/webhooks/{id}` body | (M0 不入 prompt — 当前 webhook 只是触发器，body 未喂给 agent) | 外部系统 | ⚪ M0 不动 |
| Cron 调度路径 | trigger.config.seed_input(从 DB 读) | DB 直连改写者 | ✅ **drift defense** — 即使 API 被绕过 |

**威胁分类**：

| 威胁 | 攻击者画像 | 攻击效果 |
|------|----------|---------|
| Classic prompt injection("ignore all previous instructions") | 恶意 tenant 用户 / 共享 tenant 内的低权限 user | agent 行为偏离设计 |
| 隐形 Unicode 注入(ZWJ / RTL override 等 17 字符) | 高级攻击者；可绕过基于字符串匹配的人工审查 | 同上 + 难以肉眼审计 |
| Exfil 指令(curl/wget 拼接 env vars) | 恶意 tenant 用户 | 跨 turn 触发数据外泄 |
| Role hijack("you are now ...") | 恶意 tenant 用户 | 篡改 agent 身份认知 |
| DB drift(SQL 注入 / 内部人员绕 API) | 内部人员 / 库被攻破 | 修改已审核的 trigger，cron 下次 fire 时执行恶意 prompt |

**Trust boundaries**：
- **API 入口**：tenant 用户 → control-plane API，跨边界，必扫
- **DB → fire**：DB 读出 → orchestrator，**理论上**已审过，但为防 drift 仍轻量扫
- **scheduler tick**：scheduler 在内进程仅传 trigger_id，但 fire_trigger 读 DB 后还原 trigger，所以扫点在 fire_trigger 内

### 2.2 双层扫描设计

**Layer A — Create/Update Strict(Block)**：
- **位置**：`api/triggers.py` 的 `create_trigger` + `patch_trigger`，在 `_validate_config` 之后
- **scope**：`strict`(全模式集，含 SSH 后门 / 持久化 / 配置文件改写)
- **行为**：命中即 `HTTPException(422)`，response body `{"detail": "...prompt_injection..."}`，emit `TRIGGER_PROMPT_INJECTION_BLOCKED` audit
- **扫的字段**：`body.name`、`body.config.seed_input`(若有)、`body.config` 的所有 `str` 值(递归)
- **为什么 strict**：创建期是 tenant 用户能 intervene 的最后机会；宽扫合规要求高

**Layer B — Fire-time Context(Warn + Audit)**：
- **位置**：`trigger_firing.py` 的 `fire_trigger`，在拼 `seed_text` 之后、构造 `HumanMessage` 之前
- **scope**：`context`(中等模式集，去掉 SSH 后门那种"用户写代码常见但当前文本可疑"的)
- **行为**：命中默认仅 emit `TRIGGER_PROMPT_INJECTION_WARN` audit + 继续 fire；命中 `severity=block` 模式时(配置可控)拒 fire + emit
- **为什么 context**：drift 场景下，已经走到 fire 这一层；硬 block 影响业务，warn + audit 让 SecOps 后审；如果 tenant config 设了 `strict_fire_block: true` 才升级到 block
- **manifest 字段**：`tenant_config.trigger_fire_scan_mode: "warn" | "block"`，默认 `"warn"`

**Mini-ADR U-2：strict 创建期 block + context 运行期 warn**

- **决定**：两层 scope 分级，create-time 严，fire-time 宽
- **替代方案 1**：两层都 strict + block → 拒绝(DB drift 不是常见场景，命中即 block 影响业务；强 block 应集中在 tenant 能 intervene 的创建期)
- **替代方案 2**：仅 create-time 扫 → 拒绝(没有 drift defense；SQL 注入 / 内部人员场景无防护)
- **替代方案 3**：tenant manifest 全控开关 → 拒绝(M0 强制 baseline，避免 tenant 关掉这层)
- **配置面**：仅 fire-time 的 block/warn 升级 tenant 可控；create-time 不可关

### 2.3 实施细节

**文件清单**：

| 文件 | 改动 |
|------|------|
| `packages/helix-common/src/helix_agent/common/threat_patterns.py` | 新建(per § 1.1) |
| `packages/helix-common/tests/test_threat_patterns.py` | 新建：移植 Hermes test 矩阵 + helix 特定(隐形 Unicode 16 个 codepoint 个个覆盖) |
| `packages/helix-protocol/src/helix_agent/protocol/audit.py` | 加 5 个 AuditAction(per § 1.2) |
| `packages/helix-protocol/src/helix_agent/protocol/tenant_config.py` | 加字段 `trigger_fire_scan_mode: Literal["warn", "block"] = "warn"` |
| `services/control-plane/src/control_plane/api/triggers.py` | `_validate_config` 之后加 `_scan_trigger_create_strict()` 调用 |
| `services/control-plane/src/control_plane/trigger_firing.py` | 在拼 `seed_text` 后加 `_scan_seed_text_context()` 调用 |
| `services/control-plane/tests/test_triggers_api.py` | 加 ~15 个测试(strict 命中 / 不同字段命中 / 拒绝信息脱敏) |
| `services/control-plane/tests/test_trigger_firing.py` | 新建 / 加 ~10 个测试(fire 命中 warn / 命中 block) |
| `packages/helix-persistence/migrations/versions/00XX_trigger_fire_scan_mode.py` | 新迁移：`ALTER tenant_config ADD trigger_fire_scan_mode VARCHAR(8) NOT NULL DEFAULT 'warn'` |

**代码骨架(伪)**：

```python
# api/triggers.py 内
async def create_trigger(...) -> JSONResponse:
    _validate_config(body.kind, body.name, body.config)

    findings = _scan_trigger_strict(body.name, body.config)   # 新增
    if findings:
        await emit(audit, ..., action=AuditAction.TRIGGER_PROMPT_INJECTION_BLOCKED,
                   details={"scope": "strict", "findings": [f.to_dict() for f in findings]})
        raise HTTPException(status_code=422, detail="prompt blocked by injection scanner")
    # ... 原有 quota / persist / emit 逻辑
```

```python
# trigger_firing.py 内
async def fire_trigger(...) -> UUID | None:
    # ... 原有 agent build 逻辑
    seed = trigger.config.get("seed_input")
    seed_text = seed if isinstance(seed, str) and seed.strip() else f"Scheduled run of trigger '{trigger.name}'."

    findings = scan_for_threats(seed_text, scope="context")   # 新增
    if findings:
        mode = await _get_fire_scan_mode(tenant_id=trigger.tenant_id)   # tenant_config 读
        action = (AuditAction.TRIGGER_PROMPT_INJECTION_BLOCKED if mode == "block"
                  else AuditAction.TRIGGER_PROMPT_INJECTION_WARN)
        await emit(audit_logger, ..., action=action,
                   details={"scope": "context", "findings": [f.to_dict() for f in findings],
                            "trigger_id": str(trigger.id)})
        if mode == "block":
            logger.error("trigger_firing.scan_blocked", extra={"trigger_id": str(trigger.id)})
            _triggers_fired_blocked.inc()
            return None
    # ... 原有 graph_input / worker 启动逻辑
```

### 2.4 拒绝信息脱敏

**安全考虑**：返回 422 时不能把命中模式 ID 完整暴露给攻击者(否则攻击者可用作 oracle 调 prompt 绕过)。

**实现**：
- response body 仅 `{"detail": "prompt blocked by injection scanner; see audit log for details"}`
- audit log 内有详细 `findings`(SecOps 可看)
- log 行用 `logger.warning("trigger.scan_blocked", extra={"trigger_id": ..., "field": ..., "pattern_count": N})` (不写 pattern_id 进 log，避免日志泄漏)

### 2.5 测试矩阵

**单测(helix-common)**：
- [ ] `scan_for_threats` 17 个隐形 Unicode codepoint 单独命中
- [ ] 三个 scope 模式分级正确(`strict` 包含 `context` 包含 `all` 包含 `all`)
- [ ] `ThreatFinding.excerpt` 截断 ≤ 200 字符
- [ ] 空字符串 / 仅空白返 `[]`
- [ ] 误判矩阵：合法 prompt 文本(从 K.K12 eval baseline 抽 50 条)全部 `[]`(否则模式需收紧)

**集成测(control-plane)**：
- [ ] `POST /v1/triggers` 含 "ignore all previous instructions" → 422 + audit `TRIGGER_PROMPT_INJECTION_BLOCKED`
- [ ] `POST /v1/triggers` `config.seed_input` 含 ZWJ 字符 → 422
- [ ] `POST /v1/triggers` `name` 含 RTL override → 422
- [ ] `POST /v1/triggers` 合法 prompt → 201
- [ ] `PATCH /v1/triggers/{id}` 把 seed_input 改成恶意 → 422
- [ ] 422 response body **不含** pattern_id(仅通用文案)
- [ ] cron tick 触发 fire，DB 内 trigger 含恶意(模拟 drift) + tenant_config `warn` → fire 成功 + audit `WARN`
- [ ] 同上 + tenant_config `block` → fire 返 None + audit `BLOCKED` + run 未创建

### 2.6 可观测

| Metric | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `helix_uplift_threat_scan_total{scope, result}` | counter | scope=strict/context, result=clean/blocked/warned | 总 scan 次数 |
| `helix_uplift_threat_pattern_hit_total{pattern_id, scope}` | counter | pattern_id, scope | 模式级命中分布 |
| `helix_uplift_triggers_blocked_total{phase}` | counter | phase=create/fire | trigger 被 block 次数 |

| Prometheus recording rule | 用途 |
|---------------------------|------|
| `helix:uplift:threat_block_rate:5m = rate(helix_uplift_threat_scan_total{result="blocked"}[5m])` | 异常飙升告警(SecOps SLO) |
| `helix:uplift:trigger_fire_warn_rate:1h = rate(helix_uplift_triggers_blocked_total{phase="fire"}[1h])` | drift 检测信号(应该接近 0) |

### 2.7 关键决策点(开发期可能踩)

1. **`config` 递归扫到哪一层？** —— 目前 `config: dict[str, Any]`，所有 `str` 叶子节点都扫；如果某 trigger kind 未来加大段非 prompt 文本(如 webhook body template)，需要白名单字段
2. **零误杀 vs 漏防御 trade-off** —— Hermes 模式相对保守(明确 attack 词汇)，但仍可能误杀创意写作类 prompt；先 K.K12 baseline 跑误判矩阵，超过 1% 收紧模式
3. **威胁模式更新走 PR 流程的 review 速度** —— 模式更新窗口期太长是 Risk；约定模式更新 PR 必走 security label + 24h SLA(per [memory:zero-tech-debt](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md))

### 2.8 Sprint #1 验收清单

- [ ] `helix_agent.common.threat_patterns` 模块上线，5 个公开 API 全 typed + docstring
- [ ] 5 个新 AuditAction 在 protocol 和 control-plane 双 Literal 一致(per audit-literal-drift memory)
- [ ] `_scan_trigger_strict` 在 `create_trigger` + `patch_trigger` 接入；fire 期 context 扫接入
- [ ] tenant_config `trigger_fire_scan_mode` 字段 migration 应用 dev/staging
- [ ] 单测 ≥ 95%；集成测覆盖 § 2.5 全部矩阵
- [ ] Prometheus 4 个 metric + 2 个 recording rule 上线
- [ ] K.K12 eval baseline 重跑无退化 ≥ 5%
- [ ] `docs/runbooks/threat-scanner-tuning.md` 新建：模式更新流程 / 误判分析步骤 / staging fixture 集

---

## 3. Sprint #2 — Memory 投毒防御 + drift detection

> **依赖前置**：✅ Sprint #1 已 merge(commit `442cd69`，PR #307)。本 Sprint 复用 `helix_agent.common.threat_patterns`(strict scope + 17 invisible Unicode 表)+ `control_plane.uplift.threat_metrics`(counter helper)+ runbook `threat-scanner-tuning.md`(同一份 SecOps 流程)。
>
> **复用范围**：不引入新的扫描器,不动 threat_patterns 注册表;只是把 Sprint #1 的库装到第二组 entry-points(memory write / recall)+ 引入一条新数据完整性能力(drift 检测)。

### 3.1 威胁模型

**Attack surface 枚举**(全 memory 子系统所有进入用户 prompt 的可控输入):

| 入口 | 字段 | 控制者 | 当前防御 |
|------|------|--------|----------|
| `PATCH /v1/memory/{id}` (`api/memory.py:141`) | `body.content` (≤ 4000 字符) | tenant 用户 | ❌ 仅 length cap,无内容扫描 |
| `memory_writeback_node`(`orchestrator/graph_builder/memory.py:170`) | LLM 提取的 `content` 串 | tenant 用户(经 LLM 间接) | ❌ |
| `MemoryWritebackDLQ` 重试(`memory/dlq_worker.py:175`) | 同上(失败 writeback 重试) | 同上 | ❌ |
| `MemoryStore.retrieve()` → `recalled_memories` → 渲染进 system prompt(`graph_builder/memory.py:137`) | DB 持久化的 `content` | tenant 用户 + 内部人员(SQL 注入 / 直连 DB) | ❌ |
| DB 直连改 `memory_item.content` | DB 列 | 内部人员 / 库被攻破 | ✅ 已有 `content_hash` 列(K.K7),但 **未做读时校验** |

**威胁分类**:

| 威胁 | 攻击者画像 | 攻击效果(per-user 持久 agent 场景) |
|------|----------|---------|
| 用户经 PATCH 直接写恶意 memory | 恶意 tenant 用户 | 后续所有 session 的 recall 喂毒 prompt → agent 长期偏移 |
| LLM 抽取 trajectory 中混入的 promptware → writeback | 高级攻击者(把恶意指令塞进 tool 输出 / 上传的文档) | 同上,但绕过用户主观意愿(用户没主动写) |
| DB drift(SQL 注入 / DBA 篡改) | 内部人员 / 高级攻击 | 绕过创建期审核,持久注入 |
| 模式集更新发现历史 memory 命中新模式 | (非攻击,运营场景) | recall 时被 redact;用户可见,可重写 |

**与 Sprint #1 trigger 威胁的差异**(决定 Sprint #2 设计选择):

| 维度 | Trigger(Sprint #1) | Memory(Sprint #2) |
|------|---------------------|-------------------|
| Lifetime | 单次 fire 完毕 | **跨 session 持久**(per-user 持久 agent 核心) |
| 影响面 | 1 个 run 偏离 | **所有未来 sessions** + 跨 agent 实例 |
| 入口数 | 2(create/patch + fire) | 5(API + writeback + DLQ + retrieve + DB drift) |
| Drift 风险 | 低(trigger 一旦审过基本不变) | **高**(memory 一直被读写,DB 暴露面大) |
| 适合的 fail-mode | block(drift) / warn(默认) | **block(write)** / **redact(recall)** |

### 3.2 双层扫描设计 + drift 检测

**Layer A — Write(strict + 全量 block)**:

- **位置**:`MemoryStore.write()`(`packages/helix-persistence/src/.../memory/sql.py` + `memory.py`)→ 内置 strict 扫
- **行为**:遇到任一 finding → 该 batch 整体拒绝 + raise `MemoryInjectionBlockedError` + 一并 emit `MEMORY_INJECTION_BLOCKED` 每条 item;**不进行部分写入**(batch atomicity)
- **同时**:`api/memory.py` PATCH 预扫一次(给用户 422 oracle-safe 通用文案),失败前不调 embedder(省 OpenAI 调用)
- **同时**:`orchestrator/graph_builder/memory.py::memory_writeback_node` 捕 `MemoryInjectionBlockedError` → DLQ 跳过(不 retry,不丢已学的合法 items — 进 DLQ dead-letter 让 SecOps review)
- **理由**:写入是创作期,用户有完整 intervene 通道;LLM 写入也应严格,否则 promptware 永久落地

**Layer B — Recall(strict + redact,**不**block**)**:

- **位置**:`memory_recall_node` 在 `memory_store.retrieve()` 之后、写 `recalled_memories` 之前
- **行为**:每条返回 memory 扫一遍 → 命中即把该 item 的 `content` 替换为 `[BLOCKED:<category>]` 占位符,**留在 list 中** → 再 emit `MEMORY_INJECTION_REDACTED`
- **不删除 DB 行**:live state 保留 → user 可 UI 看到 + 自决定 `DELETE /v1/memory/{id}`(per K.K6 forget)
- **理由**:DB drift 场景下 silent-block 会丢用户可见性;redact 同时给安全 + UX
- **递归 walk vs 仅 content**:memory.content 是 plain `str`,不需要递归

**Drift 检测(轻量,无 schema 变更)**:

- **位置**:`MemoryStore.retrieve()` 内部
- **行为**:对每条返回 item,重算 `sha256(lower(trim(content)))` → 与 `content_hash` 列比对 → 不一致即:
  1. emit `MEMORY_DRIFT_DETECTED` audit(`details: {memory_id, stored_hash_prefix, computed_hash_prefix, kind}`,**不写明文 content**)
  2. 该条仍走 Layer B redact 流程(替占位符,因为内容已不可信)
  3. 在 SecOps dashboard 形成飙升告警
- **不做** drift backup / 自动 restore(per § 0.3 out-of-scope,M1 escape hatch)
- **理由**:`content_hash` 列已经存在(K.K7),零 schema 变更;recompute 是 O(N) 算法不影响 retrieve 延迟(典型 top_k=10)

**Mini-ADR U-3:write block / recall redact 的边界**

- **决定**:write 期 strict + block;recall 期 strict + redact(留 live state)
- **替代方案 1** write + recall 都 block:拒 — DB drift 场景下用户看不到"我以前的 memory 哪去了",sup ux 灾难
- **替代方案 2** write + recall 都 redact:拒 — 写入是创作期,strict 模式拒一条用户重写就好;redact 写入会让用户困惑"我刚写的怎么是占位符"
- **替代方案 3** recall scope=context 而非 strict:拒 — memory lifetime 是跨 session 持久的,污染影响远超 trigger;高强度模式合适

**Mini-ADR U-4:drift 检测在 retrieve 内做,不做 background sweep**

- **决定**:`MemoryStore.retrieve()` 内部 lazy 校验,无独立 worker
- **替代方案 1** background sweep 全表轮巡:拒 — 大租户 memory 表可能上百万行,定期全扫成本高;且 drift 通常在攻击后第一次 recall 时立即出现,lazy 检测时效性等价
- **替代方案 2** DB trigger:拒 — 跨数据库可移植性差(M0 only Postgres,但 testcontainers 测试 InMemory store 也要走同一逻辑)
- **替代方案 3** retrieve 前先 SELECT WHERE content_hash != sha256(content):拒 — Postgres 的 hash 函数和 Python `hashlib.sha256` 在 normalization 上有差(`lower()` / unicode NFC 等),容易假阳

### 3.3 关键 ADR(归档,实施期不可推翻)

参见 § 3.2 末尾 **U-3 + U-4** 两条 Mini-ADR。

### 3.4 实施细节

**文件清单**:

| 文件 | 改动 |
|------|------|
| `packages/helix-persistence/src/.../memory/base.py` | 加 `MemoryInjectionBlockedError` 异常 + 拓展 `MemoryStore.retrieve()` 返回类型(从 `list[MemoryItem]` → `list[ScannedMemoryItem]` 或加 `redacted: bool` 字段?见 § 3.6 决策) |
| `packages/helix-persistence/src/.../memory/sql.py` | `SqlMemoryStore.write()` + `.retrieve()` 接入扫描 |
| `packages/helix-persistence/src/.../memory/memory.py` | `InMemoryMemoryStore.write()` + `.retrieve()` 同上 |
| `packages/helix-persistence/tests/test_*_memory_store.py` | unit 测试矩阵(§ 3.5) |
| `services/control-plane/src/control_plane/api/memory.py` | PATCH 预扫,接 422 + audit,oracle-safe 文案 |
| `services/control-plane/src/control_plane/memory/dlq_worker.py` | 捕 `MemoryInjectionBlockedError` → dead-letter 不重试 |
| `services/orchestrator/src/orchestrator/graph_builder/memory.py` | `memory_recall_node` 后处理:redact + audit;`memory_writeback_node` 捕 block 异常 |
| `services/control-plane/src/control_plane/uplift/threat_metrics.py` | 加 3 个 memory counter |
| `packages/helix-protocol/src/helix_agent/protocol/audit.py` | 加 3 个 AuditAction(已在 § 1.2 预声明) |
| `tools/observability/rules/uplift.yml` | 加 memory drift / redact rate recording rules + alerts |
| `docs/runbooks/threat-scanner-tuning.md` | 加 § 8 memory drift 响应流程 |

**代码骨架(伪)**:

```python
# packages/helix-persistence/.../memory/sql.py 内
class MemoryInjectionBlockedError(ValueError):
    """raised by MemoryStore.write() when any item's content fails strict scan."""

async def write(self, items: Sequence[MemoryItem]) -> None:
    blocked: list[tuple[MemoryItem, list[ThreatFinding]]] = []
    for item in items:
        findings = scan_for_threats(item.content, scope="strict")
        if findings:
            blocked.append((item, findings))
    if blocked:
        # 一次性 emit 全部命中的 audit(不依赖外部 audit logger,放 _audit_emitter callback)
        for item, findings in blocked:
            await self._emit_audit(item, AuditAction.MEMORY_INJECTION_BLOCKED, findings)
        raise MemoryInjectionBlockedError(
            f"{len(blocked)}/{len(items)} memory items blocked by strict scan"
        )
    # ... 原有 batch insert(每行附带 content_hash)
```

```python
# graph_builder/memory.py 内 memory_recall_node
memories = await memory_store.retrieve(...)
redacted_memories: list[MemoryItem] = []
for m in memories:
    findings = scan_for_threats(m.content, scope="strict")
    if findings:
        await audit_emit(MEMORY_INJECTION_REDACTED, m, findings)
        redacted_memories.append(m.model_copy(update={
            "content": f"[BLOCKED:{findings[0].category}]"
        }))
    else:
        redacted_memories.append(m)
return {"recalled_memories": redacted_memories}
```

```python
# packages/helix-persistence/.../memory/sql.py 内 retrieve drift check
async def retrieve(self, ...) -> list[MemoryItem]:
    rows = await self._query(...)
    out: list[MemoryItem] = []
    for row in rows:
        computed = sha256(row.content.lower().strip().encode()).hexdigest()
        if computed != row.content_hash:
            await self._emit_audit_drift(row)
            # 同步走 redact 路径(由 recall 节点拿到后处理)
            # 这里仅保留 row,标记 drift 通过返回值传递
        out.append(row_to_item(row))
    return out
```

**为什么 audit 在 store 层而非节点层**:
- 三个入口(API PATCH / DLQ retry / writeback node)都走 store 写入,统一在 store 不容易遗漏
- store 接 audit logger 需要新依赖注入,通过新的 `AuditEmitter` Protocol 注入(类似 SecretStore 的方式),避免 store 直接 import control-plane audit

### 3.5 测试矩阵

**单测(persistence 包)**:
- [ ] `SqlMemoryStore.write()` 含 1 条 prompt_injection → 整 batch raise + audit 全条 emit
- [ ] `SqlMemoryStore.write()` 含 1 条 invisible Unicode → raise
- [ ] `SqlMemoryStore.write()` 全清 → 正常 insert
- [ ] `InMemoryMemoryStore.write()` 同上 3 条(parity)
- [ ] `SqlMemoryStore.retrieve()` DB 内容篡改 → emit drift + content 不变(redact 由调用方做)
- [ ] `SqlMemoryStore.retrieve()` 含恶意 content → emit redacted + content 仍是原文(parser 拿到原文,节点层再 redact)
- [ ] `content_hash` 计算用 NFC normalize 还是 raw?— 与 K.K7 现有行为一致

**集成测(control-plane API)**:
- [ ] `PATCH /v1/memory/{id}` 含 classic injection → 422 + audit BLOCKED + memory 行不变(不写入)
- [ ] `PATCH /v1/memory/{id}` 含 ZWJ → 422
- [ ] `PATCH /v1/memory/{id}` 422 body 不含 pattern_id / matched substring(oracle 防御同 #1)
- [ ] `PATCH /v1/memory/{id}` 合法 → 200(正常 update + embed)

**集成测(orchestrator memory node)**:
- [ ] DB 预置含恶意 content 的 memory → recall → `recalled_memories[i].content == "[BLOCKED:<category>]"` + audit REDACTED
- [ ] DB drift(直接 UPDATE 表)→ recall → audit DRIFT + content 被 redact
- [ ] 清白 memory → 不 audit + 不 redact + content 原样喂 agent

**集成测(DLQ + writeback)**:
- [ ] writeback LLM 提取的 1 条含 injection → `write()` raise → DLQ enqueue(per K.K7 已有逻辑)→ 下次 retry 同样 raise → 5 次后 dead-letter
- [ ] writeback 5 条全清 → 正常 write

### 3.6 开发期需决策点

下面 3 条是设计 review 期需要你拍板的:

1. **`MemoryStore.retrieve()` 返回类型怎么传递"drift detected"信号给调用方?**
   - 选项 A:在 `MemoryItem` 加 transient `drift: bool` 字段(per-call 不持久化)
   - 选项 B:返回 `tuple[list[MemoryItem], list[UUID drifted_ids]]`
   - 选项 C:不传递,recall 节点重算一次 hash(性能略差但接口干净)
   - **推荐 A**:零冗余计算,API 干净;`drift` 字段默认 False,只在 retrieve 期设
2. **writeback LLM 提取的 batch 部分命中 → 部分写入 vs 整 batch 拒?**
   - 选项 A:整 batch 拒(blob 拒绝)
   - 选项 B:per-item filter,clean 的写入 + dirty 的 audit
   - **推荐 A**:简单 + 攻击场景下"部分写入"反而留下污染线索给攻击者迭代;LLM 重新 extract 一次成本很低
3. **`memory_recall_node` redact 时占位符格式**
   - 选项 A:`"[BLOCKED:<category>]"`(类别名,如 `injection` / `c2`)
   - 选项 B:`"[BLOCKED:memory:<memory_id_prefix>]"`(指向具体行,user 可对应到 UI)
   - 选项 C:不告诉 agent 是什么 — 直接整条 memory 从 list 里去掉
   - **推荐 A**:category 给 agent 足够上下文知道是个被屏蔽的内容(可决定要不要 ask user 重新提供),不暴露 pattern_id(oracle defense)

### 3.7 可观测

| Metric | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `helix_uplift_memory_writes_blocked_total{source}` | counter | source=api/writeback/dlq | 写入被拒次数 |
| `helix_uplift_memory_recalls_redacted_total` | counter | — | recall 期被 redact 的 item 累计 |
| `helix_uplift_memory_drift_total` | counter | — | drift 检测次数 |

| Recording rule | 用途 |
|----------------|------|
| `helix:uplift:memory_drift_rate:1h = rate(helix_uplift_memory_drift_total[1h])` | drift 飙升 = 真攻击信号 |
| `helix:uplift:memory_redact_rate:1h = rate(helix_uplift_memory_recalls_redacted_total[1h])` | recall 期发现历史 memory 命中模式(可能是模式集刚 deploy) |

| Alert | severity | for | 触发条件 |
|-------|----------|-----|----------|
| `HelixUpliftMemoryDriftDetected` | **P0** | 15min | `memory_drift_rate:1h > 0` 持续 — drift 几乎只可能是攻击 |
| `HelixUpliftMemoryRedactSpike` | P1 | 30min | `memory_redact_rate:1h > 1` 持续 — 异常多的 recall 命中 |

### 3.8 Sprint #2 验收清单

- [ ] `MemoryInjectionBlockedError` 异常 + `MemoryStore.write()` strict 扫拦截在 sql/memory 双实现一致
- [ ] `MemoryStore.retrieve()` content_hash 重算 drift 检测在 sql 实现(InMemory 跳过 drift,因为不会被外部改)
- [ ] `PATCH /v1/memory/{id}` 接入预扫 + oracle-safe 422
- [ ] `memory_recall_node` 接入 redact 逻辑 + audit emit
- [ ] `memory_writeback_node` 捕 `MemoryInjectionBlockedError` → DLQ enqueue 走 dead-letter 路径(不重试)
- [ ] 3 个新 AuditAction(`MEMORY_INJECTION_BLOCKED` / `MEMORY_INJECTION_REDACTED` / `MEMORY_DRIFT_DETECTED`)在 protocol Enum 上线
- [ ] 3 个 Prometheus counter + 2 个 recording rule + 2 个 alert 上线
- [ ] `docs/runbooks/threat-scanner-tuning.md` 新增 § 8(memory drift 响应步骤)
- [ ] 单测 ≥ 95% / 集成测覆盖 § 3.5 全部矩阵
- [ ] K.K12 eval baseline 重跑无退化 ≥ 5%
- [ ] 零债 6 条全过(per § 0.4)

### 3.9 与 Sprint #1 的复用矩阵

| Sprint #1 产出 | Sprint #2 复用方式 |
|---------------|-------------------|
| `helix_agent.common.threat_patterns.scan_for_threats` | 直接调用(scope=strict) |
| `helix_agent.common.threat_patterns.ThreatFinding` | 同上 |
| `control_plane.uplift.threat_metrics.record_*` | 扩展(加 memory counter) |
| `control_plane.uplift.threat_scan.scan_payload_strict` | **不复用**(memory 是单 str,不需要递归 walk + 10KB cap) |
| `docs/runbooks/threat-scanner-tuning.md` | 扩展(加 § 8) |
| 422 oracle-safe 文案模式 | 复用思想(memory PATCH 文案统一) |
| Mini-ADR U-1(模块位置) | 锁定 — 不动 |
| Mini-ADR U-2(strict block / context warn) | **不复用** — memory 走 U-3(strict block / strict redact) |

---

## 4. Sprint #3 — Skill 附属文件(references/templates/scripts)

> **本章节 stub** — Sprint #3 开工前补完。预计 Week 7-10。

主要设计要点(占位)：
- `skill_version` schema 加 `supporting_files: JSONB`(单 skill ≤ 5MB) + ObjectStore 兜底
- `skill_manager_tool` 加 `write_file` / `remove_file` action
- `agent_factory` 按需暴露 `skill_view(name, "references/xxx.md")` 工具
- 直接抄 Hermes 的目录约定(references=session 细节 / templates=可复用 / scripts=可执行)
- ZIP import/export 扩展子目录支持
- Admin UI Skills page 加附属文件浏览(per [memory:admin-ui-design-baseline](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_admin_ui_design_baseline.md))

---

## 5. Sprint #4 — Curator 自动状态机(基础设施提前 + 启用 M1)

> **本章节 stub** — Sprint #4 开工前补完。预计 Week 11-12。

主要设计要点(占位)：
- `SkillRow` 加 `pinned: bool` + `last_activity_at: timestamptz`
- 新建 `services/control-plane/src/control_plane/skill_curator.py`：周期 worker 纯启发式三态转移
- 默认阈值 30 天 → stale / 90 天 → archived，per-tenant 可配
- Admin UI Skills page 加 pin 操作 + 状态显示
- **启用调参等 M1-K J.7b-1 agent 自创建上线后 2-4 周再做**(本 Sprint 不动)

---

## 6. Sprint #5 — MCP Server(暴露给 Claude Code / Cursor)

> **本章节 stub** — Sprint #5 开工前补完。预计 Week 4-6。

主要设计要点(占位)：
- 新建 `services/control-plane/src/control_plane/mcp_server.py`：FastMCP 包装现有 API
- **M0 仅暴露读权限工具**：conversations_list / conversation_get / messages_read / events_poll / events_wait / channels_list(6 个)
- 写权限工具(messages_send / permissions_respond)推 M1-I
- auth 复用现有 OIDC，新增可选 MCP-specific token(per-user 隔离)
- RLS 复用 tenant_scope；system_admin 跨租户路径正常工作 + 必 audit(per [memory:stream-n-cross-tenant-admin](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_stream_n_cross_tenant_admin.md))

---

## 7. Sprint #6 — Memory Hybrid Retrieval(向量 + 全文 RRF)

> **执行顺序**:本项作为 Sprint **#3 执行**(完全无依赖,无新设计风险,可直接 port J.5 现成代码;比 #3 / #5 / #8 启动成本更低)。
>
> **依赖前置**:✅ Sprint #1 + #2 已 merge(commit `442cd69` + `be5e6ed`)。本 Sprint 复用 J.5 RAG 子系统的成熟代码,不引入新算法。

### 7.1 背景

helix M0 的 `MemoryStore.retrieve()` 仅做向量召回(pgvector cosine distance)。已知短板:

- **精确词匹配丢失**:用户上次说 "use error code E-2031";向量化后 "E-2031" 不一定排进 top-k(尤其是 episodic 类 memory 较长时)
- **CJK 中文召回弱**:helix 已有 user 用中文 memory(per dogfood)。中文 query 经 embedder 后语义召回偏,关键词召回(jieba 分词)能补
- **多模态 caption / OCR 文本召回低**:vision 上传的 caption 文本里的命名实体也是同样问题

J.5 RAG 子系统在 `knowledge_chunk` 上已经走了完整的 hybrid path(向量 + 全文 + RRF + 可选 LLM rerank)— see `services/orchestrator/src/orchestrator/tools/knowledge.py` § 124-156。port 到 memory 是直接的代码搬运,**不开新模型 / 不新增依赖**。

### 7.2 范围 & 边界

#### 7.2.1 In-scope

| 子项 | 实现内容 | 关联 |
|------|---------|------|
| **#6.1 Schema** | migration `0040_memory_item_content_tsv`:`memory_item` 加 `content_tsv` TSVECTOR 列 + GIN 索引;app-side 用 `tokenize_for_search()`(已存在,K.K7 用过)填充 | 复用 J.5 模式 |
| **#6.2 Store 写时维护** | `MemoryStore.write()` 写入 `content_tsv`(SQL + InMemory parity);`update_content()` 同步更新;`SqlMemoryStore` 用 `func.to_tsvector('simple', tokenize_for_search(content))` | InMemory 用 `tokenize_for_search` token set 做关键词查 |
| **#6.3 Store 读时 hybrid** | `MemoryStore.retrieve()` 新增 `query_text: str \| None = None` 参数。`None` → 旧 vector-only 行为(backward compat);`str` → 双路召回 + RRF 融合,top-N 返回 | Mini-ADR U-5 |
| **#6.4 RRF 提到 helix-common** | 从 `orchestrator/tools/knowledge.py:264` 抽出 `_rrf_fuse` → `helix-common/src/.../search/rrf.py`(新模块);knowledge.py 改用 helix-common 版,memory 新代码也用 | Mini-ADR U-6 |
| **#6.5 Recall node 接 hybrid** | `memory_recall_node` 读 `tenant_config.memory_recall_mode`(新字段);默认 `hybrid` 时把 user task text 作 `query_text=` 传给 store;`vector` 时不传 | per-tenant escape hatch |
| **#6.6 Tenant config 字段** | migration `0041_memory_recall_mode`:`tenant_config.memory_recall_mode VARCHAR(8) NOT NULL DEFAULT 'hybrid'` + CHECK constraint(`IN ('hybrid','vector')`);protocol layer 加 `MemoryRecallMode` Literal | per Sprint #1 同模式 |
| **#6.7 Observability** | 3 个 metric:`helix_uplift_memory_retrieval_total{mode,result}` / `helix_uplift_memory_hybrid_rrf_overlap` histogram / `helix_uplift_memory_retrieval_latency_seconds` histogram;recording rule `helix:uplift:memory_hybrid_adoption_ratio:1h`(衡量启用情况) | uplift_metrics 扩展 |
| **#6.8 Eval baseline** | `tools/eval/per_user_isolation.py` 加 hybrid 模式 baseline 对比(vector vs hybrid 在 50 个 memory + 20 query 上的 hit ratio / MRR)|  |
| **#6.9 Runbook** | `docs/runbooks/threat-scanner-tuning.md` 加 § 9:hybrid 退化排查 + tenant 切回 vector 的步骤 |  |

#### 7.2.2 Out-of-scope(明确推迟)

| 推迟项 | 落地 | 备注 |
|-------|------|------|
| LLM reranker(J.5 已有) | M1 或更晚 | memory recall top_k 通常 ≤ 5,rerank 价值边际;且 rerank 引入 1 个额外 LLM call/recall,成本不接受 |
| 第三种召回(BM25 / SPLADE / ColBERT) | 永不做 | 不在能力 gap 内;vector + tsvector 覆盖 90% 案例 |
| Per-user 个性化 RRF k 值 | M1 dogfood 数据后 | M0 锁标准 RRF `k=60`(知识子系统已验证) |
| `memory_recall_mode = "keyword_only"` 选项 | 永不做 | 没有合理用例(纯关键词 = vector 退化版) |
| 自动从向量 baseline 切到 hybrid 默认的"灰度开关" | 永不做 | 默认 hybrid + tenant opt-out 已经是灰度控制 |

#### 7.2.3 验收(Sprint Exit)

1. migration 0040 + 0041 在 dev/staging 应用成功;rollback 测试通过
2. `MemoryStore.retrieve()` 双签名(`query_text=None` / `query_text=<str>`)在 SQL + InMemory 等价
3. `memory_recall_node` 在 hybrid 模式下两路召回 + RRF;在 vector 模式下回退纯向量
4. RRF 共享模块 `helix_agent.common.search.rrf` 上线;`knowledge.py` 改用共享版,行为不变
5. eval baseline:hybrid 相对 vector 在 K.K12 上 hit ratio 提升 ≥ 10%(否则推后实施,改进算法)
6. 零债 6 条全过
7. CI 全绿 + CodeQL 无新增

### 7.3 架构

#### 7.3.1 数据流

```
                    AgentState["messages"]                       (last HumanMessage = task)
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
       embed(task)     tokenize(task)    tenant_config.memory_recall_mode 决定走哪条
              │               │
              │               │
              ▼               ▼
  MemoryStore.retrieve(query_embedding=..., query_text=task)
              │
        ┌─────┴──────┐
        ▼            ▼
   vector hit     keyword hit          (each capped at recall_limit ~= 20)
        │            │
        └─────┬──────┘
              ▼
        RRF fuse (k=60)
              │
              ▼
       top-N MemoryItem
              │
              ▼
        recall node (Sprint #2 redact)
              │
              ▼
        AgentState["recalled_memories"]
```

#### 7.3.2 Mini-ADR U-5:`MemoryStore.retrieve(query_text=None)` 双签名

- **决定**:retrieve 新参数 `query_text: str | None = None`。`None` 走旧 vector-only(backward compat — 老调用方不受影响);`str` 走 hybrid(双路召回 + RRF)
- **替代方案 1** 拆 `retrieve_vector` / `retrieve_hybrid` 两个方法:拒 — 调用方决策点从 store API 转移到 caller,违反 single point of decision;且 backward compat 复杂
- **替代方案 2** retrieve 永远 hybrid:拒 — 老路径(write-back 流程的 memory recall 调用)不需要 hybrid;还会引入不必要的 keyword 查询成本
- **替代方案 3** retrieve 加 `mode` 参数:拒 — `mode` 隐式依赖 `query_text` 是否提供,容易传错;`query_text=str` 自带"模式"语义
- **InMemory 实现策略**:`query_text=str` 时用 Python `set()` 取 token 交集做关键词侧的 ranking(不需要数据库);跟 SQL 实现 RRF 结果一致(同样的 RRF k=60)

#### 7.3.3 Mini-ADR U-6:RRF 提到 `helix_agent.common.search.rrf`

- **决定**:从 `orchestrator/tools/knowledge.py:264` 抽出 `_rrf_fuse` 到新模块 `packages/helix-common/src/helix_agent/common/search/rrf.py`;knowledge.py 改 import,memory 新代码同样 import
- **泛型设计**:`def rrf_fuse[T: Hashable](rankings: Sequence[Sequence[T]], *, k: int = 60) -> list[T]` — `T` 由调用方决定(可以是 `KnowledgeChunk` / `MemoryItem` / `UUID`);只要 hashable 即可
- **替代方案 1** 复制粘贴一份到 memory 模块:拒 — 两份要同步,违反 DRY;此前 Sprint #2 用 `helix-common` 已经验证过这种共享是干净的
- **替代方案 2** 放 `helix-persistence`:拒 — 不依赖任何持久化逻辑,纯算法
- **替代方案 3** 放 `orchestrator`:拒 — memory 在 store 层就要用,反向依赖

#### 7.3.4 InMemory store hybrid 实现

`InMemoryMemoryStore.retrieve(query_text=str)`:
1. 复用现有 cosine 排序得到 `vector_hits: list[MemoryItem]`(top recall_limit)
2. 简易关键词排序:对 candidates(所有非 deleted_at row,filter by tenant/user/kind)做 `tokens = set(tokenize_for_search(content).split())`;query tokens 也 split;`overlap = len(tokens & query_tokens)`,按 overlap 倒序取 top recall_limit → `keyword_hits`
3. `rrf_fuse([vector_hits, keyword_hits])[:limit]`
4. 仍走 `_with_drift_flag()`(Sprint #2)

InMemory 不追求与 PG `to_tsvector` 100% 一致(那是工程实现细节);追求**对外契约一致**:hybrid 返回更精确的混合 ranking,vector 返回纯向量 ranking。

### 7.4 实施细节

**文件清单**:

| 文件 | 改动 |
|------|------|
| `packages/helix-common/src/.../search/rrf.py` | **新建** — 泛型 `rrf_fuse[T]` |
| `packages/helix-common/src/.../search/__init__.py` | **新建** — 模块包初始化 |
| `packages/helix-common/tests/test_rrf.py` | 新建 — 移植 `knowledge.py` 测试覆盖 + 泛型场景 |
| `packages/helix-persistence/migrations/versions/0040_memory_item_content_tsv.py` | 新建 — 加 TSVECTOR 列 + GIN 索引(参考 0022) |
| `packages/helix-persistence/migrations/versions/0041_memory_recall_mode.py` | 新建 — `tenant_config.memory_recall_mode` 加列(同 0039 模式) |
| `packages/helix-persistence/src/.../models/memory_item.py` | ORM 加 `content_tsv` Mapped 列(用 sqlalchemy.dialects.postgresql.TSVECTOR) |
| `packages/helix-persistence/src/.../memory/sql.py` | `write()` 填 `content_tsv`;`update_content()` 同步;新参数 `query_text` |
| `packages/helix-persistence/src/.../memory/memory.py` | InMemory parity(Python token set 实现) |
| `packages/helix-persistence/src/.../memory/base.py` | `retrieve` 抽象签名加 `query_text` |
| `packages/helix-persistence/tests/test_in_memory_memory_store.py` | 加 6-8 个 hybrid 测试 |
| `packages/helix-persistence/tests/test_sql_memory_store.py` | 加 4-5 个 hybrid 测试(docker) |
| `packages/helix-protocol/src/.../tenant_config.py` | 加 `MemoryRecallMode` Literal + 字段 |
| `packages/helix-persistence/src/.../models/tenant_config.py` | ORM 加列 |
| `packages/helix-persistence/src/.../tenant_config/sql.py + memory.py` | upsert / row_to_record 扩展 |
| `services/orchestrator/src/.../graph_builder/memory.py` | `make_memory_recall_node` 加 `tenant_config_store` 可选参数 + 读 mode |
| `services/orchestrator/src/.../tools/knowledge.py` | 改 import 用 `helix_agent.common.search.rrf`,删本地 `_rrf_fuse` |
| `services/orchestrator/tests/test_memory_nodes.py` | 加 hybrid mode 测试 |
| `services/orchestrator/tests/test_knowledge_tool.py` | 已用 `_rrf_fuse` 间接;验证 import 改后行为不变(已有 5 个测试覆盖) |
| `packages/helix-common/src/.../uplift_metrics.py` | 加 3 个 retrieval counter / histogram |
| `tools/observability/rules/uplift.yml` | recording rule:hybrid adoption ratio |
| `tools/eval/per_user_isolation.py` | 加 hybrid 模式 baseline 对比 |
| `docs/runbooks/threat-scanner-tuning.md` | § 9 hybrid 退化排查 |

**关键代码骨架(伪)**:

```python
# helix-common/src/.../search/rrf.py
def rrf_fuse[T](rankings: Sequence[Sequence[T]], *, k: int = 60) -> list[T]:
    scores: dict[T, float] = {}  # T must be hashable
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda t: scores[t], reverse=True)
```

```python
# helix-persistence/.../memory/sql.py
async def retrieve(self, *, tenant_id, user_id, query_embedding,
                   query_text: str | None = None, kind=None, limit=5):
    if query_text is None:
        # backward-compat — pure vector
        return await self._vector_retrieve(...)
    vector_hits = await self._vector_retrieve(..., limit=self._recall_limit)
    keyword_hits = await self._keyword_retrieve(..., query_text=query_text,
                                                  limit=self._recall_limit)
    fused = rrf_fuse([vector_hits, keyword_hits])[:limit]
    return [_with_drift_flag(...) for row in fused]

async def _keyword_retrieve(self, ...):
    tokenized = tokenize_for_search(query_text)
    if not tokenized:
        return []
    ts_query = func.plainto_tsquery("simple", tokenized)
    stmt = (
        select(MemoryItemRow)
        .where(
            MemoryItemRow.tenant_id == tenant_id,
            MemoryItemRow.user_id == user_id,
            MemoryItemRow.deleted_at.is_(None),
            MemoryItemRow.content_tsv.op("@@")(ts_query),
        )
        .order_by(func.ts_rank(MemoryItemRow.content_tsv, ts_query).desc())
        .limit(limit)
    )
    ...
```

```python
# orchestrator/.../graph_builder/memory.py memory_recall_node
async def memory_recall_node(state, config):
    ...
    mode = await _resolve_recall_mode(tenant_id, tenant_config_store)
    if mode == "hybrid":
        memories = await memory_store.retrieve(
            tenant_id=..., user_id=..., query_embedding=vec, query_text=task, limit=top_k,
        )
    else:
        memories = await memory_store.retrieve(
            tenant_id=..., user_id=..., query_embedding=vec, limit=top_k,
        )
    redacted = [_redact_memory(m) for m in memories]
    return {"recalled_memories": redacted}
```

### 7.5 关键决策点(开发期可能踩)

下面是 review 期需要拍板的:

1. **InMemory 关键词排序是否严格匹配 SQL 行为?**
   - 选项 A:严格 — 用 Python 模拟 `to_tsvector` 行为(stopwords / weights / etc),保证 testcontainers 测和 InMemory 测结果完全一致
   - **选项 B(推荐)**:不严格 — InMemory 用简易 token set overlap;`MemoryStore` contract 只承诺"hybrid mode 比 vector mode 在 mixed query 上召回更好",不承诺 RRF 排序逐行一致
   - 理由:严格模拟成本高(jieba + tsvector stopwords 行为复杂)且不增加正确性 — InMemory 是测试 fixture,产线行为以 SQL 为准
2. **RRF `k` 值锁 60 还是 per-recall_path 可配?**
   - **选项 A(推荐)**:全局 `k=60`(知识子系统已验证)
   - 选项 B:为 memory 单独 `k=30`(更激进偏向高排名 item;memory top-k 通常 5,vs knowledge 20)
   - 理由:M0 数据不足以判断 30 vs 60 哪个更好;锁 60 与 J.5 一致;M1 跑 dogfood 数据后再调
3. **`memory_recall_mode` 字段默认值**
   - **选项 A(推荐)**:`hybrid` 默认(per Sprint 1/2 经验:默认安全/高质量行为 + opt-out)
   - 选项 B:`vector` 默认(per-tenant 显式 opt-in)
   - 理由:hybrid 是更好的召回,降级到 vector 是 fallback;新 tenant 直接享受新能力
4. **`memory_recall_node` 是否需要 `audit_logger` 注入?**
   - **选项 A(推荐)**:不注入。recall 走 hybrid / vector 是性能优化路径,不是安全事件,无需 audit row;Prometheus counter 足够
   - 选项 B:每次 recall 都 emit audit:噪音过大(每个 turn 一次),冲淡有用的 audit 数据
5. **K.K12 baseline 退化 ≥ 5% 是否仍 merge?**
   - **选项 A(推荐)**:不 merge;改进算法(可能是 `k` 值或 recall_limit)再交付
   - 选项 B:merge,接受 5% 退化:拒 — hybrid 的目的是提升召回,退化反而说明实现有问题

### 7.6 测试矩阵

**单测(helix-common)**:
- [ ] `rrf_fuse` 单 ranking 退化为 identity
- [ ] `rrf_fuse` 两 ranking 全相同 → 与单 ranking identity
- [ ] `rrf_fuse` 两 ranking 全相反 → 中间值
- [ ] `rrf_fuse` 泛型 — 用 UUID / dataclass / 自定义类(参考 knowledge.py 现有测试)
- [ ] `rrf_fuse(rankings=[], k=60) == []` edge case

**单测(persistence InMemory)**:
- [ ] hybrid 模式比 vector 模式在精确词命中场景排名更高(seed memory "user error code E-2031",query "error code E-2031" → hybrid 排第 1)
- [ ] hybrid 模式 query_text 为空 → 退化为 vector
- [ ] hybrid 模式中文 query(jieba 分词)正常工作
- [ ] hybrid 模式空结果集 → 返 []
- [ ] vector 模式(`query_text=None`)行为与 Sprint #2 完全一致(backward compat)

**集成测(persistence SQL,docker)**:
- [ ] hybrid 模式精确词命中
- [ ] hybrid 模式 RRF 排序与 InMemory 大致一致(top-3 相同;不要求逐行)
- [ ] tsvector 列 GIN 索引建成 + EXPLAIN ANALYZE 命中 index
- [ ] write/update_content/retrieve hybrid 在并发下数据一致(已有 Sprint #2 的 fixture)

**集成测(orchestrator)**:
- [ ] memory_recall_node hybrid 模式调用走 hybrid path(用 spy 验证)
- [ ] memory_recall_node vector 模式调用走 vector-only path
- [ ] 缺失 tenant_config_store(向后兼容)→ 默认 hybrid 但 silently 退到 vector(对接收 None 友好)
- [ ] 测试默认值 — 新 tenant(无 tenant_config 行)→ hybrid 默认生效

**eval baseline(`tools/eval`)**:
- [ ] `per_user_isolation.py` 加 50 个 fact + episodic memory + 20 query
- [ ] vector 模式 hit@5 / MRR baseline 锁定
- [ ] hybrid 模式 hit@5 / MRR baseline ≥ vector + 10%(否则 Sprint exit 失败)

### 7.7 可观测

| Metric | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `helix_uplift_memory_retrieval_total{mode,result}` | counter | mode=vector/hybrid, result=hit/miss | 记录调用分布 |
| `helix_uplift_memory_hybrid_rrf_overlap` | histogram | — | vector ∩ keyword overlap 比例(衡量 hybrid 价值) |
| `helix_uplift_memory_retrieval_latency_seconds{mode}` | histogram | mode=vector/hybrid | 退化预警:hybrid 显著慢于 vector |

| Recording rule | 用途 |
|----------------|------|
| `helix:uplift:memory_hybrid_adoption_ratio:1h = sum(rate(memory_retrieval_total{mode="hybrid"}[1h])) / sum(rate(memory_retrieval_total[1h]))` | 看 hybrid mode 在所有 tenant 中的采用率 |

### 7.8 与 Sprint #1/#2 的复用矩阵

| 已有产出 | Sprint #6 复用方式 |
|---------|-------------------|
| `helix_agent.common.threat_patterns` | 不动 |
| `helix_agent.common.uplift_metrics` | 扩展(加 3 个 retrieval metric) |
| `MemoryStore.retrieve()` 接口 | **扩展**(加 query_text 参数,backward compat) |
| `MemoryItem.drift` field(Sprint #2) | 保留;hybrid path 同样走 `_with_drift_flag()` |
| `_redact_memory()` 函数(Sprint #2 recall node) | 保留;hybrid / vector mode 都经它过 |
| `tenant_config.trigger_fire_scan_mode` 模式 | 类比:`memory_recall_mode` 同模式(default + per-tenant opt-out) |
| `tokenize_for_search` (J.5) | 直接复用 |
| `KnowledgeStore.keyword_search` SQL pattern | 直接 port 到 `_keyword_retrieve()` |
| `_rrf_fuse` (J.5) | **抽出**到 helix-common,J.5 + memory 共用 |
| Sprint #1 + #2 oracle-safe 422 模式 | hybrid 不涉及用户拒绝(纯读),不需要 |
| Sprint #1 + #2 preflight 流程 | 严格遵循(per Sprint #1 教训) |

### 7.9 Sprint #6 验收清单

- [ ] 2 migrations 应用 dev/staging;rollback 测试通过
- [ ] `helix_agent.common.search.rrf` 上线;`knowledge.py` 改用,5 个旧测试不退化
- [ ] `MemoryStore.retrieve(query_text=)` 双签名在 SQL + InMemory 等价
- [ ] `memory_recall_node` 接 `tenant_config.memory_recall_mode`,默认 hybrid
- [ ] 3 个 Prometheus metric + 1 个 recording rule 上线
- [ ] eval baseline:hybrid hit@5 ≥ vector + 10%
- [ ] 零债 6 条全过
- [ ] CI 全绿 + CodeQL 无新增 high/critical
- [ ] `docs/runbooks/threat-scanner-tuning.md` § 9 新增

---

## 8. Sprint #7 — Memory 短期 → 长期自动凝结

> **本章节 stub** — Sprint #7 开工前补完。预计 Week 11-13。

主要设计要点(占位)：
- 新建 `services/control-plane/src/control_plane/memory_consolidator.py`：识别"反复出现的事实" trigger 信号
- 凝结 LLM 调用：辅助 model(如 Haiku)总结 N 轮窗口 → 写 long-term memory
- 防误学约束(参考 Hermes Skill review prompt 的 4 条分类：环境性失败 / 负面工具断言 / session-specific transient errors / one-off task narratives)
- M2-C archive 流水线接口预留(per [memory:complete-not-minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md))
- **触发策略 + 阈值 M1 dogfood 数据反过来调**(本 Sprint 不动)

---

## 9. Sprint #8 — Memory Frozen Snapshot / 前缀缓存优化

> **本章节 stub** — Sprint #8 开工前补完。预计 Week 5-6(与 #5 / #6 并行)。

主要设计要点(占位)：
- `memory_recall_node` 加 "frozen snapshot" 模式：per-session 召回一次 + 整 session 复用(vs 默认 per-turn)
- L.L1 prompt caching middleware 适配，cache_control 加在 memory block 末尾(per [STREAM-L-DESIGN.md § L1](./STREAM-L-DESIGN.md))
- manifest `policies.memory_recall_mode: "per_turn" | "per_session"`
- 默认仍 `per_turn`；`per_session` 启用条件 M1 评估 token 成本数据

---

## 10. 整体节奏

```
Week 1                  Week 2                  Week 3
─────────────────────────────────────────────────────
#1 Cron 注入扫描 ████
                ↓ 抽威胁模式库到 helix-common
#2 Memory 投毒防御              ████████

Week 4                  Week 5                  Week 6
─────────────────────────────────────────────────────
#5 MCP Server              ████████████
#6 Memory hybrid           ████████
                           (port J.5)
#8 Memory frozen snapshot          ████████

Week 7                  Week 8                  Week 9-10
─────────────────────────────────────────────────────
#3 Skill 附属文件          ████████████████
                          (含 ZIP 扩展)

Week 11                 Week 12                 Week 13
─────────────────────────────────────────────────────
#4 Curator 状态机           ████████
   (基础设施完整 + 启用走 J.7b-1 节奏)
#7 Memory 凝结引擎              ████████████████
   (引擎本体 + 防误学，触发策略 M1 调)
```

**关键依赖**：
- **#1 先做**(威胁模式库被 #2 复用) — 顺序锁定
- **#5 / #6 / #8 / #3 完全并行**(无相互依赖)
- **#4 / #7 在 sprint 后期做基础设施**(启用按 M1-K J.7b-1 节奏)

---

## 11. Risk 与缓解(Sprint 级)

### Risk 1：12-13 周 Sprint 单人扛不住
- **缓解**：拆"前 6 周(#1 #2 #5 #6 #8)+ 后 6-7 周(#3 #4 #7)"两个子 sprint，中间留 1-2 周 dogfood observation
- 或：双人并行可压到 6-8 周

### Risk 2：Sprint 期间 dogfood 30 天平行参考点被搅
- **缓解**：每项 PR merge 前 fresh staging 数据集回放 K.K12 eval baseline；任何 baseline 退化 ≥ 5% 卡 PR

### Risk 3：#4 / #7 基础设施可能浪费(若 J.7b-1 / M1 dogfood 数据出来后发现设计需重做)
- **缓解**：#4 / #7 按"模块化 + 可关闭" 设计(manifest 字段 enabled=true/false)，即使阈值大改也不会推翻基础设施
- 接受 10-20% 重做风险换 6-12 个月提前价值

### Risk 4：#3 / #4 schema 变更同期跑
- **缓解**：先 #3 上 main 跑 1 周观察，再上 #4；schema 变更不并行

### Risk 5：威胁模式误杀合法 prompt
- **缓解**：K.K12 baseline 抽 50 条 prompt 做误判矩阵；模式更新 PR 必跑该矩阵；超过 1% 误杀收紧模式

---

## 12. 与 ITERATION-PLAN 的对照

| Sprint 编号 | ITERATION-PLAN 主归属 | 关联归属 |
|------------|---------------------|---------|
| #1 | § M0→M1 Gate § Capability Uplift Sprint | — |
| #2 | § M0→M1 Gate § Capability Uplift Sprint | — |
| #3 | § M0→M1 Gate § Capability Uplift Sprint | M1-K J.7b-6(标"已并入") |
| #4 | § M0→M1 Gate § Capability Uplift Sprint(基础设施) | M1-K J.7b-1(启用调参) |
| #5 | § M0→M1 Gate § Capability Uplift Sprint(读权限工具) | M1-I(写权限工具) |
| #6 | § M0→M1 Gate § Capability Uplift Sprint | — |
| #7 | § M0→M1 Gate § Capability Uplift Sprint(基础设施) | M2-C(archive 对接) + M1 策略调优 |
| #8 | § M0→M1 Gate § Capability Uplift Sprint | M1(默认启用条件) |

---

— EOF —
