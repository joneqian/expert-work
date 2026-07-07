# 4.4 Agent 自动演化 Skill：enablement + 真模型 E2E（★4→★5）

> T1 第二项（用户拍板）。**驳回 Explore 的「1-3 周建 SE-4」误判**：演化管线代码**整条完整**、
> 真 graph replay 已接 lifespan、SE-9 基准已存在。★4→★5 = **纯 enablement + 真模型 E2E 证飞轮闭环 +
> 修 E2E 暴露的 gap**，同 11.4/11.5 B 路径，**不是建东西**。

## 1. 现状（勘探+亲核，驳回 Explore）

| 组件 | 文件 | 状态 |
|---|---|---|
| CurationWorker（轨迹+反馈→candidate） | `curation_worker.py` | ✅ 完整，**默认 ON**，CI 测 |
| SkillEvolutionWorker（drain candidate） | `skill_evolution_worker.py` | ✅ 完整，**gate OFF** |
| distiller / attributor（aux LLM） | `skill_distiller.py` / `skill_attribution.py` | ✅ |
| **真 graph replay**（baseline vs treatment 真 LangGraph） | `orchestrator/evolution/graph_runner.py:GraphReplayTaskRunner` + `skill_evolution_wiring.py:_GraphReplayInvoker` | ✅ **真实，已接 lifespan**（fake 仅 CI） |
| 蒸馏判决 / promotion gate（rate+breaker+审计） | `skill_evolution_assembly.py` / `skill_promotion_gate.py` | ✅ |
| **SE-9 基准**（闭环+反 gaming+安全臂+SLO） | `tools/eval/self_evolution.py` | ✅ 确定性 CI 证控制流 |
| 演化 review admin-ui | `SkillsList/Detail.tsx` + `SkillEvolutionKillSwitch.tsx` | ✅ |

**唯一闸**：`enable_skill_evolution_worker=False`（`settings.py:366`）+ aux 模型须有平台凭证
（`memory_consolidator_default_aux_provider` in `effective_platform_provider_credentials`，`app.py:1118`）。

**真 gap 一句话**：管线全在、CI fake 全测，但**从没用真模型在真栈跑过一遍飞轮**——★5 缺的是「开 + 真模型 E2E 证 distill→replay→judge→promote 闭环」+ 修 E2E 暴露的接缝。

## 2. ★5 范围（B 路径式）

1. **Ungate（compose 透传，同 #650）**：现 compose 对这些全无透传，加：
   - `EXPERT_WORK_ENABLE_SKILL_EVOLUTION_WORKER`（默认 false）
   - `EXPERT_WORK_SKILL_EVOLUTION_WORKER_INTERVAL_S` / `EXPERT_WORK_CURATION_WORKER_INTERVAL_S`（E2E 调低）
   - `EXPERT_WORK_MEMORY_CONSOLIDATOR_DEFAULT_AUX_PROVIDER` / `_AUX_MODEL`（dev 设 deepseek，须有平台凭证）
   - `EXPERT_WORK_ENABLE_SKILL_ROLLBACK_MONITOR`（可选）
2. **真模型 E2E 证飞轮**（驱动全链，seeding 重叠故顺带证 curation）：
   - 跑真 agent N 次产 success trajectory（自然 seeding）+ `POST /v1/sessions/{id}/feedback` 给 up → positive_feedback 信号
   - CurationWorker（低 interval）→ PENDING candidate
   - SkillEvolutionWorker（低 interval）→ **真 aux LLM distill DRAFT skill → 真 graph replay baseline-vs-treatment → 真 judge grounding 判决 → promotion gate 决策**
   - 查 skill store（DRAFT/ACTIVE）+ 审计 + 演化 admin-ui
3. **修 E2E 暴露的 gap**（同 11.5 逮容器 import / REFUSAL_TEXT）。

## 3. E2E 成功判定（诚实）

★5 真模型证明 = 飞轮**用真模型端到端执行到 grounded 决策**，非必须 auto-promote：

- **必达**：真 aux LLM 蒸出 DRAFT skill（distill 真路径通）+ 真 graph replay baseline-vs-treatment 跑起来
  （replay 真路径通）+ 到达 grounded 判决 + promotion 决策（judge+gate 通）。
- **加分（happy path）**：精心造「无 skill 失败 / 有 skill 成功」场景 → replay grounds delta>0 → auto-promote。
- **非确定性诚实交代**：真 LLM 蒸馏可能 `no_draft`、replay 可能 `inconclusive`（held-out 薄 / 无可测 delta）——
  这些**也是有效 E2E 结果**（机制通，据实报告，不调参美化），同 11.5 injection-003 的处理。

## 4. E2E 难点（已勘探，见对话）

- 链串 3 异步 worker（agent→curation 300s→evolution 600s），靠**调低 interval**驱动（同 eval worker，非 API 触发）。
- 要预置：active AgentSpec + success trajectory（跑真 agent 自然产）+ ThreadMeta + feedback + held-out（eval golden 或 trajectory fallback）。
- **真杀手 = 非确定性**：真 LLM 是否从轨迹蒸出可用 skill + replay 是否显出可测 delta（p<0.05 grounding）——
  得**精心造场景**（无 skill 解不了、有 skill 一步搞定的可复用套路）。这是工程 crux，两种走法都躲不掉。

## 5. 驱动方式

写 E2E driver（`tools/eval/verify_evolution.py` 或 inline，同 verify_live 模式）：起真栈 + 平台凭证 + enable 两 worker + 低 interval；
跑 agent 产轨迹 + 反馈 → 轮询 candidate → 轮询 DRAFT skill / 判决 → 查 promotion + 审计。模型 key 留用户，env 传不打印。

## 6. 不做

- 不改演化核心逻辑（distill/replay/judge/promote 代码不动，除非 E2E 暴露 bug）。
- 不动 SE-9 基准（已存在）。
- 不强求 auto-promote（grounded 决策即证机制；happy path 加分）。
- rollback monitor 深验（SE-7d）后续，本期可选开。
