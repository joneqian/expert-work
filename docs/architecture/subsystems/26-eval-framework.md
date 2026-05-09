# 26 Eval Framework — 数据集、指标、A/B gate、CI 集成、golden set 管理

> 把"上线 = 改 prompt 后人工抽查"升级为"每个 manifest version 有 golden set 自动评测、A/B gate 阻止质量回退、用户反馈闭环回流到数据集"。核心：声明式 EvalSet + 标准化指标 + CI 集成 + 数据治理。

---

## 1. 职责 & 边界

### ✅ 做
- `EvalSet` 声明式数据集（YAML，git 版本化）
- 标准指标：accuracy（LLM-as-judge）、latency P95、cost、safety violation、regression
- A/B gate：新 manifest version 上线前自动跑 EvalSet；不通过则禁止 promote 到 production
- CI 集成：PR merge 前异步触发，结果作为 PR comment（不阻塞 merge）
- golden set 治理：难度分级、版本化、维护者 ≥ 2 人、数据敏感走 PII redactor
- 用户反馈闭环：thumbs up/down + 文本 → 候选 case → 人工审核 → 进 golden set
- 离线评测（M2）/ 在线 A/B（M3）

### ❌ 不做
- 不做 prompt 自动优化（M3+，需要 RL / DSPy 类）
- 不做模型选型 ranking（属于 [10 LLM Gateway](./10-llm-gateway.md)）
- 不做实时质量监控告警（属于 [20 Observability](./20-observability.md)）
- 不做用户反馈 UI 收集 → 业务运营层 / SDK 提供 API
- 不做 manifest 自动改写（人工驱动）

---

## 2. 上下游依赖

| 依赖方向 | 子系统 | 关系 |
|---------|--------|------|
| 上游 | CI/CD | 触发 eval run（PR / pre-promote） |
| 上游 | Control Plane | 暴露 EvalSet CRUD、EvalRun list/detail |
| 下游 | Orchestrator | eval runner 调 orchestrator 跑 agent（mocked LLM 或真实 LLM） |
| 下游 | LLM-as-judge provider | 通常用更强模型（如 sonnet 评 haiku） |
| 横切 | [10 LLM Gateway](./10-llm-gateway.md) | eval 流量计入独立 quota（不耗生产 quota） |
| 横切 | [13 Memory Store](./13-memory-store.md) | eval 走独立 collection（不污染生产记忆） |
| 横切 | [17 Audit Log](./17-audit-log.md) | EvalSet 修改、强制 promote 写审计 |
| 横切 | [20 Observability](./20-observability.md) | eval metric / dashboard |
| 横切 | PII Redactor | 用户反馈进 golden set 前必过 redactor |

---

## 3. 数据模型 / 状态机

### 3.1 EvalSet 声明（YAML）

```yaml
apiVersion: helix.io/v1
kind: EvalSet
metadata:
  name: ticket-classifier-baseline
  version: "2.3"                                    # 与 git tag 对齐
  tenant: customer-success
  agent_ref: ticket-classifier@^0
  maintainers:                                       # 必须 ≥ 2 人
    - alice@example.com
    - bob@example.com
spec:
  source: "production_sampled_2026-04"               # 数据来源标注
  pii_redacted: true                                 # 必须为 true（生产抽样）
  cases:
    - id: case-001
      difficulty: easy                               # easy | medium | hard
      input:
        ticket_text: "I can't login to my dashboard"
      expected:
        category: technical
        priority: medium
      tags: [login, authentication]

    - id: case-002
      difficulty: hard
      input:
        ticket_text: "..."                          # 模糊或多意图
      expected:
        category: billing
      judge_hint: |
        若分类为 billing 或 feedback 都视为可接受；abuse 视为错误。

  thresholds:                                        # A/B gate 准入条件
    accuracy_min_delta: -0.05                        # 不得低于 baseline 5%
    cost_max_ratio: 1.20                             # 不得高于 baseline 1.2 倍
    latency_p95_max_ratio: 1.30
    safety_violation_max: 0                          # 任何 safety 违规直接 fail
```

### 3.2 状态机（EvalRun）

```
QUEUED → RUNNING ──┬──▶ PASSED       （达标）
                   ├──▶ FAILED       （未达 gate）
                   ├──▶ ERROR        （执行异常）
                   └──▶ CANCELLED    （人工取消）
```

### 3.3 Postgres DDL

```sql
CREATE TABLE eval_set (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL,
    agent_ref       TEXT NOT NULL,                  -- 'ticket-classifier@^0'
    git_ref         TEXT NOT NULL,                  -- commit sha
    spec            JSONB NOT NULL,                 -- 完整 EvalSet
    case_count      INT NOT NULL,
    maintainers     TEXT[] NOT NULL,
    source          TEXT NOT NULL,
    pii_redacted    BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name, version)
);

CREATE TABLE eval_run (
    id                 UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    eval_set_id        UUID NOT NULL REFERENCES eval_set(id),
    agent_name         TEXT NOT NULL,
    candidate_version  TEXT NOT NULL,                -- 被测 manifest version
    baseline_version   TEXT,                          -- 对比基线（如生产当前版本）
    state              TEXT NOT NULL,                -- QUEUED/RUNNING/PASSED/FAILED/ERROR/CANCELLED
    triggered_by       TEXT NOT NULL,                -- ci_pr / pre_promote / manual
    triggered_actor    TEXT,                         -- JWT subject
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    summary            JSONB,                        -- {accuracy: 0.93, cost_usd_micro: ..., ...}
    gate_decision      TEXT,                         -- pass / fail / not_evaluated
    fail_reason        TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON eval_run (tenant_id, agent_name, state, created_at);

CREATE TABLE eval_case_result (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES eval_run(id),
    case_id         TEXT NOT NULL,
    difficulty      TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    accuracy_score  NUMERIC(4,3),                    -- 0..1，LLM-as-judge 给分
    judge_reason    TEXT,
    actual_output   JSONB,
    latency_ms      INT NOT NULL,
    tokens_input    INT NOT NULL DEFAULT 0,
    tokens_output   INT NOT NULL DEFAULT 0,
    cost_usd_micro  BIGINT NOT NULL DEFAULT 0,
    safety_flags    TEXT[],                          -- ['pii_leak', 'jailbreak'] 等
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON eval_case_result (run_id, passed);

CREATE TABLE eval_feedback_candidate (
    id              UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    session_id      UUID NOT NULL,
    agent_name      TEXT NOT NULL,
    feedback_type   TEXT NOT NULL,                   -- thumbs_down / text / regression
    payload_redacted JSONB NOT NULL,                 -- 已过 PII redactor
    state           TEXT NOT NULL,                   -- PENDING_REVIEW / ADMITTED / REJECTED
    reviewer        TEXT,
    target_eval_set TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`eval_case_result` 索引：`(run_id, passed)`。

### 3.4 Pydantic schema（核心字段）

```python
class EvalCase(BaseModel):
    id: str
    difficulty: Literal["easy","medium","hard"]
    input: dict
    expected: dict
    judge_hint: str | None = None
    tags: list[str] = []

class EvalThresholds(BaseModel):
    accuracy_min_delta: float = -0.05
    cost_max_ratio: float = 1.20
    latency_p95_max_ratio: float = 1.30
    safety_violation_max: int = 0

class EvalSet(BaseModel):
    name: str
    version: str
    agent_ref: str
    maintainers: list[str]            # ≥ 2 强校验
    source: str
    pii_redacted: bool
    cases: list[EvalCase]
    thresholds: EvalThresholds
```

---

## 4. 关键接口

### 4.1 Python（runner）

```python
class EvalRunner:
    async def run(self, eval_set: EvalSet, candidate: AgentSpec, baseline: AgentSpec | None = None) -> EvalRunSummary: ...
    async def judge(self, case: EvalCase, output: dict) -> JudgeResult: ...
    async def gate(self, run_id: UUID) -> GateDecision: ...
```

### 4.1.1 EvalSet 上传流程（PII 双重验证）

```
client POST /v1/eval-sets
   ↓
[1] schema 校验（Pydantic）
   ↓
[2] maintainer ≥ 2 强校验
   ↓
[3] PII detector 同步扫描（同一引擎，与 CI 步骤一致）   ← 关键决策
       ├─ 命中：拒绝上传，返回 422 + 命中字段路径列表
       └─ 通过：标记 `pii_redacted=true`
   ↓
[4] 持久化 eval_set 行；emit audit `action=eval:write`
   ↓
[5] CI（PR pre-merge）跑同一 detector → 双重验证（防 detector 配置漂移、防 staging 上传后 git 又改）
```

**关键决策**：**PII detector 在上传时同步跑（不只 CI）**——理由：CI 是异步 + 跑在不同 detector 配置版本下，存在"上传成功但 CI 拒绝"的窗口；窗口内 EvalSet 已落库 + 可被其他 user 读取（哪怕暂时不能 promote），相当于**在 server 端短暂暴露生产抽样的 PII**。同步检测把窗口缩到 0；CI 是双重验证（catch detector 升级后旧数据回归）。失败时返回完整命中路径（`spec.cases[0].input.user_email` 等）便于 maintainer 快速定位。

### 4.2 HTTP API

```
POST /v1/eval-sets                                    # 创建 / 上传新版本
GET  /v1/eval-sets/{name}?version=...
POST /v1/eval-sets/{name}/cases                       # 单 case 增量

POST /v1/eval-runs                                    # 触发评测
       Body: {eval_set_id, candidate_version, baseline_version?, triggered_by}
       → 202 {run_id}

GET  /v1/eval-runs/{run_id}                           # 状态 + summary
GET  /v1/eval-runs/{run_id}/cases                     # 详情列表

POST /v1/eval-feedback                                # 业务侧上报反馈
       Body: {session_id, feedback_type, text}
       → 202 candidate_id

POST /v1/eval-feedback/{id}:admit                     # reviewer 同意进 golden set
       Body: {target_eval_set, normalized_input, normalized_expected}
```

### 4.3 CLI

```
helix eval run --set ticket-classifier-baseline --candidate ./manifest.yaml
helix eval gate --run <run_id>                  # 退出码 0=pass, 1=fail
helix eval list --agent ticket-classifier
```

---

## 5. 算法 / 关键决策

### 5.1 LLM-as-judge

**关键决策**：**用更强模型评估弱模型输出**——例如 candidate 用 haiku，judge 用 sonnet 或 opus。

输入 judge：`case.input` + `case.expected`（结构化）+ candidate output + `case.judge_hint`（可选）；输出严格 JSON schema：`{accuracy_score: 0..1, passed: bool, reasoning: "<= 200 字"}`，`accuracy_score >= 0.8` 即 passed。

**关键决策**：**judge 只判 expected 字段**，不评"风格"——风格类用人工评测；自动化 eval 只关心可校验的目标字段。

**关键决策**：**eval 调 [10 LLM Gateway](./10-llm-gateway.md) 时强制透传 tenant**——不仅 candidate 的 LLM 调用，**LLM-as-judge 也带原 case 所属 tenant**（不允许用平台超级 tenant）。理由：

- **quota 隔离**：eval 流量必须计入该 tenant 的独立 eval bucket（[10 § 5.3](./10-llm-gateway.md) / [16 § 4](./16-quota-rate-limit.md)），不串到其他 tenant，也不把 judge 流量算成"平台公共开销"导致后续争议
- **audit 不串**：[17 audit_log](./17-audit-log.md) 的 LLM 调用记录按 tenant 索引；judge 调用属于 EvalSet 所属 tenant 的合规留痕
- **PII 边界**：judge 看到的 `case.input` / candidate output 包含 EvalSet 所属 tenant 的（已 redact 的）数据，必须在该 tenant 的 PII pattern 上下文里被 redactor 二次过滤
- **purpose 标签**：调用同时带 `purpose=eval` 与 `purpose=eval_judge`（区分 candidate vs judge），让 [10 § 5.2](./10-llm-gateway.md) 的 routing key + [16 § 10](./16-quota-rate-limit.md) 的 metric 桶能拆开

### 5.2 指标定义

| 指标 | 计算 | 说明 |
|------|------|------|
| accuracy | `mean(case.passed)` | 所有 case 平均通过率 |
| accuracy_by_difficulty | `mean by difficulty` | 分 easy/medium/hard 看 |
| latency_p95 | `percentile(case.latency_ms, 95)` | 不含 judge 时间 |
| cost_per_case_usd | `sum(case.cost_usd_micro) / count / 1e6` | 平均单 case 成本 |
| safety_violations | `count(case.safety_flags != [])` | 任意一项 safety 违规计 1 |
| regression_count | `count(prev.passed && !curr.passed)` | 退步数（与 baseline 比） |

### 5.3 A/B gate

```
def gate(run, baseline):
    s = run.summary
    b = baseline.summary
    if s.accuracy < b.accuracy + thresholds.accuracy_min_delta:
        return Fail("accuracy regression: ...")
    if s.cost_per_case > b.cost_per_case * thresholds.cost_max_ratio:
        return Fail("cost regression: ...")
    if s.latency_p95 > b.latency_p95 * thresholds.latency_p95_max_ratio:
        return Fail("latency regression: ...")
    if s.safety_violations > thresholds.safety_violation_max:
        return Fail("safety violation: ...")
    return Pass
```

**关键决策**：**gate 默认严格 fail-closed**——任何一项不达标就拒绝 promote；admin 可走 `force_promote` 走审计流程（不是绕开评测）。

### 5.4 CI 集成

PR merge 前 GitHub Action：异步触发 `helix eval run` 拿 `run_id` → 等待 30min 内 → `helix eval report --format md` 输出到 PR comment。

**关键决策**：**CI 不阻塞 merge**——eval 慢（百级 case × LLM 调用），阻塞会拖慢迭代；merge 时 PR 看到 fail comment 即可 review；真正的阻塞在 promote-to-production 步骤。

### 5.5 Pre-promote gate

manifest 从 staging promote 到 production 时强制：control-plane 查询 agent 的 eval_set → 同步触发 eval_run → gate 通过则切流量，失败拒绝并返回 fail_reason；admin 可 `--force --reason=...` 走审计强制 promote。

### 5.6 Golden set 治理

| 规则 | 强制 |
|------|------|
| 维护者 ≥ 2 人 | EvalSet 创建 / 更新 lint 强校验 |
| 数据来源标注 | 必填（生产抽样 / 合成 / 手工） |
| 生产抽样必须 PII redacted | `pii_redacted: true` 强校验；CI 跑 PII detector 二次验证 |
| 难度分布 | 建议 easy:40%, medium:40%, hard:20%；不强制 |
| 版本化 | 与 git tag 对齐；`name@version` 不可变 |
| 删除策略 | 只能加新版本；旧版本归档不删除 |
| case 数 | 建议 ≥ 50；上限 5000（更多则切多个 set） |

### 5.7 用户反馈闭环

```
业务侧 SDK：用户给 thumbs_down → POST /v1/eval-feedback
   ↓
eval_feedback_candidate state=PENDING_REVIEW
（payload 自动过 PII redactor，去除 tenant_config.pii_fields 字段）
   ↓
reviewer（agent 维护者）每周审 backlog
   ↓
admit → 转换为 EvalCase 加进对应 set 的下一版
reject → 仅记录原因
```

**关键决策**：**反馈不自动进 golden set**——人工审核保证质量，避免 LLM 输出训练数据被毒化。

---

## 6. 失败模式 & 缓解

| 失败模式 | 影响 | 缓解 |
|---------|------|------|
| LLM-as-judge 自身不稳定 | gate 误判 | 同一 case 跑 3 次取多数；judge prompt 版本化锁定；定期校准（人工 spot check） |
| 数据集泄漏到训练（模型记住答案） | 假高分 | golden set 不公开；仅 maintainer 角色可读；判定数据漂移 |
| eval 流量耗光 LLM quota | 生产受影响 | eval 走独立 LLM Gateway routing key + 独立 quota bucket |
| eval 慢导致 promote 阻塞 | 业务发版受阻 | 并发跑（受控 N=10）；超时 30min；提供"加速套餐"租户配置 |
| baseline 缺失（首次评测） | 无法 gate | baseline 缺失时仅打分不 gate；提示 maintainer 设第一版基线 |
| safety_flags 漏报 | 安全风险漏过 gate | safety 由独立 detector（guardrails 模块）打标，不依赖 judge |
| case 漂移（业务变了 expected 还旧） | 假低分 | 维护者每季度 review；带 `last_validated_at` 字段 |
| reviewer 不及时 | 反馈积压 | 每周 backlog 报表；超过 30 天的反馈自动归档 |
| 大 case 数 + 慢 judge 卡 CI | 延迟 | 小数据集（<200 case）走 PR；大数据集仅 pre-promote 跑 |
| force_promote 滥用 | 退步上线 | 单独 RBAC + 写审计 + 周报告警 |

---

## 7. 可观测性

> 命名规范、必填字段、cardinality 约束统一遵循 [20 § 5.1–5.3](./20-observability.md)；本节仅列本子系统专属。

### 7.1 Metric

```
helix_eval_run_total{tenant,agent,state}                       counter
helix_eval_run_duration_seconds{tenant,agent}                  histogram
helix_eval_score{tenant,agent,version,metric}                  gauge
   # metric = accuracy | cost | latency_p95 | safety_violations
helix_eval_regression_total{tenant,agent}                      counter
helix_eval_gate_decision_total{tenant,agent,decision}          counter
   # decision = pass | fail | force_pass
helix_eval_force_promote_total{tenant,actor_id}                counter
helix_eval_feedback_total{tenant,agent,state}                  counter
   # state = pending | admitted | rejected
```

### 7.2 OTel span

- `helix.eval.run`（attrs：set_name, set_version, candidate_version, agent_version, baseline_version, case_count）
- `helix.eval.case`（attrs：case_id, difficulty, passed, agent_version）
- `helix.eval.judge`（attrs：judge_model, latency_ms, agent_version）
- `helix.eval.gate`（attrs：decision, fail_reason, agent_version）

所有 span 必带 `agent_version`（candidate manifest version；judge 模型本身的 version 用 `judge_model` 字段区分）。

### 7.3 Dashboard

`08-eval` Grafana dashboard：
- 每个 agent 当前最新 run 的 accuracy / cost / latency 趋势
- regression 热力图（哪些 case 频繁失败）
- gate 通过率
- force_promote 计数

---

## 8. 安全考虑

| 攻击面 | 防御 |
|--------|------|
| 攻击者上传含 prompt injection 的 case | EvalSet 修改走 RBAC（maintainer 角色）+ 双人 review；CI 跑 garak 扫描 |
| 反馈通道注入恶意 payload | redactor 中间件 + 长度上限 + reviewer 人工审 |
| 跨租户读取 EvalSet | RLS by tenant；API 强制 tenant 校验 |
| LLM-as-judge 提示词被注入（输出操纵评分） | judge 输入用结构化方式拼接，对 user-controlled 内容做转义；judge 输出强制 JSON schema 校验 |
| 数据敏感泄漏（生产抽样含 PII） | EvalSet `pii_redacted=true` 强校验；二次 PII detector 扫描 |
| force_promote 被滥用 | 单独 RBAC + 审计高亮 + 限频（每 agent 每月 ≤ 1） |
| eval 流量被用于"测试不该跑的 prompt" | quota 隔离 + 审计 |

**关键决策**：**EvalSet 是受控数据资产**——RBAC `eval:write` 单独划分，不与 manifest 编辑等同；防止业务一时兴起改基线。

---

## 9. M0 / M1 / M2 演进

### M0 —— 不做
- 仅在 manifest 字段 reserved（`eval_set_ref`）；运行时不消费

### M1 —— 不做（核心是多租户 + 灰度）
- promptfoo 做最简单评测（M0 dogfood 阶段使用，独立工具，未集成）

### M2（6-8 周）—— 完整框架
- EvalSet schema + Postgres 持久化
- EvalRunner（受控并发，LLM-as-judge）
- A/B gate + pre-promote 强制
- CI 集成（PR comment）
- 用户反馈闭环（thumbs + reviewer 审核）
- Dashboard 上线
- force_promote + 审计

### M3 —— 在线 A/B + 持续改进
- 在线 A/B：小流量真实用户（基于 manifest version 路由）
- 反馈自动聚类（LLM 提取相似 fail pattern）
- prompt 改进建议（人工驱动 + LLM 助手）
- 跨 agent 共享 eval pattern 库

---

## 10. 开放问题

1. **judge 模型选型**：固定一个（如 sonnet）还是按 tenant 配置？倾向固定（一致性 > 灵活）；可被 admin override。
2. **多次 judge 多数票**：3 次平均 vs 多数票？倾向 accuracy_score 取均值，passed 取多数票。
3. **case 自动生成**：用 LLM 合成 case 是否可行？需保证多样性 + 不重复训练数据；M3 议题。
4. **eval cost 谁付**：算 tenant 自己 quota 还是平台补贴？倾向 tenant 自己付（避免滥用），但提供"基础免费额度"。
5. **离线 vs 在线 A/B 切换**：在线 A/B（M3）需要小心隐私和用户体验；如何选 case 与 baseline？需产品 + 安全联合方案。
