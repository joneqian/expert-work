# P1-S2 Eval 平台 — 架构设计

> 日期：2026-06-13
> 配套：飞轮设计 `2026-06-13-p1-self-improving-flywheel-design.md` · 评估 `..-capability-assessment.md`
> 范围：P1 飞轮第二段（度量脊梁）。补 eval **平台/ops 层**，复用现有 capability eval 引擎。

## Context（基于运行期验证，非读码）

S2 启动前真跑了现有 eval（践行「先验证后建」），ground-truth：

- **capability eval 引擎 = 强，别重建**：`tools/eval/run_baseline.py` 真跑通，15 capability 全 PASS，
  出 pass_rate/judge_mean/recall@5/mrr@5；longmem + harness 31 单测过。`_capability`/`_judge`/
  `helix_eval` 全 functional。→ 11.1 ★5、11.2 ★5、11.7 ★5 **运行期确认**。
- **eval 平台/ops 层 = 真缺**（S2 建这层）：
  - 11.6 生产 worker：`run_baseline` 是本地 CLI，**无常驻 scheduler**；`eval_run`/`eval_case_result`
    表**不存在**（仅 `0034_eval_dataset`）。
  - 11.3 会话级指标：baseline 只出 per-capability，无 resolution_rate/goal_completion。
  - 11.5 对抗集：无 `datasets/adversarial/`。
  - 11.4 trace-based eval：只评终态，不评 10.1 的 span 树。

**修正早先「eval 近零从头建」的判断**：引擎是好的，S2 = 复用引擎 + 加平台层，比从零省得多。

## 飞轮位置

`trace(10.1 ✅) → eval 平台(S2) → evaluator-optimizer(1.3) → 自写skill(4.4)`。
S2 是度量脊梁：没有它，1.3/4.4 的改进无法量化。

## S2 建序（复用 > 新建，4 个子项）

### S2.1 — eval 平台地基（11.6，先做，最大）

**新表**（migration `0076_eval_run.py`，tenant-scoped FORCE RLS，遵 `check_rls_naming.py`）：

```
eval_run:
  id UUID PK · tenant_id UUID(RLS) · suite TEXT(m0_baseline|adversarial|<capability>)
  status TEXT(queued|running|passed|failed|error) · triggered_by TEXT(manual|ci|schedule)
  summary JSONB(pass_count/total/scores) · started_at/finished_at/created_at TIMESTAMPTZ

eval_case_result:
  id BIGSERIAL PK · run_id UUID FK→eval_run · tenant_id UUID(RLS) · capability TEXT
  case_id TEXT · passed BOOL · session_id TEXT NULL(11.3) · scores JSONB
  session_metrics JSONB NULL(11.3) · created_at TIMESTAMPTZ
```

**Store**（`persistence/eval/`，ABC + SQL 实现，仿 `persistence/skill/base.py`）：
`create_run / set_status / append_case_result / get_run / list_runs(status)`。

**EvalWorker**（`control-plane/eval_worker.py`，复用 `memory_consolidator.py`/`webhook_delivery_worker.py`
骨架 start/stop/_loop/run_once）：扫 `status=queued` 的 eval_run → 用**现有 `run_baseline` 引擎**
（`_RUNNERS` capability runners）执行 → 写 eval_case_result + summary → 状态机 queued→running→passed/
failed/error。lifespan 接线进 `control-plane/app.py`（仿 memory_consolidator 注册，`enable_scheduler` 门控）。

> 复用点：执行核 = `tools/eval/run_baseline.py` 的 capability runners + `_judge`/`_capability`；
> worker 只做「调度 + 持久化 + 状态机」，不重写 eval 逻辑。

### S2.2 — 会话级指标（11.3，小，扩 _capability）

`tools/eval/_capability.py`：`CapabilityCaseResult` 加 `session_id: str|None`；`CapabilityReport`
加 `session_metrics: Mapping[str,float]`（resolution_rate/goal_completion/escalation）。
runner 按 session 聚合 per_case → 写 eval_case_result.session_metrics。1→5。

### S2.3 — 对抗集（11.5，数据+判定）

新建 `tools/eval/datasets/adversarial/{injection,jailbreak}/m0_baseline.yaml`（EvalCase 加
`adversarial_type`/`expected_refusal` 字段）+ `_judge` 加 safety 判定（拒答=pass）。接 S2.1 worker。1→5。

### S2.4 — trace-based eval（11.4，消费 10.1 span 树）

新 `tools/eval/trace_eval.py`：用 `InMemorySpanExporter`（同 `test_react_graph_tracing.py` 范式）
捕获一次 run 的 `helix.session.run` 根 span + llm_call/tool_call child span，断言调用链
（如「期望工具被调用」「LLM 调用次数 ≤ N」「无错误 span」）。依赖 10.1（✅已交付）。1→5。

## 测试 / 验证

- S2.1：worker 单测（queued→passed 状态机、RLS 隔离、复用引擎跑通）；migration 在真 PG 集成测过。
- S2.2：session_metrics 聚合单测。
- S2.3：injection 红队样本被判 pass（拒答）。
- S2.4：trace 断言抓到调用链异常。
- 全程 preflight：`uv run pre-commit run --files` + ruff + mypy（本地 `mypy 不含 control-plane/src`，注意）。

## 提交规约

- 设计 commit（本文档 + 评估报告同步）与代码分开。
- 每子项独立 PR（S2.1→S2.4），footer `Co-authored-by: leyi`，无 Claude 署名。
- 分支 `s2-eval-platform/<子项>`。
