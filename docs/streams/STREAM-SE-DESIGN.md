# Stream SE — 自我进化 Skill(Self-Evolving Skills，设计先行)

> 落实 [docs/ITERATION-PLAN.md](../ITERATION-PLAN.md) § Stream SE。
> 在 [STREAM-J-DESIGN](./STREAM-J-DESIGN.md) § 15(J.7a skill 静态启用 / J.7b-1 agent 自著设计)与 Stream L(J.12 curation 闭环)、Stream X(平台 skill 库)既有基建之上,**把 skill 从"人写、静态启用"升级为"agent 可自我生成、被真实证据验证、有界自动演化"的一等能力**。
>
> **覆盖范围**:SE-0…SE-9 共 10 个子项(SE-0 = 本设计)。本文件是 Stream SE 总设计 —— 锁定总体架构、跨切面数据模型、实现顺序与依赖、每个子项范围/架构/接口/整合点/Mini-ADR。每个子项 PR 在此基础上做局部细化设计(设计先行规则递归适用)。

设计先行规则([memory:feedback_design_first_iteration](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_design_first_iteration.md)):所有总体架构 / 跨切面接口 / Mini-ADR 必须在编码前于本文件锁定;每个子项 PR 只执行本文件对应章节 + 其局部细化设计。

> **对标纪律**:openclaw / deer-flow / hermes-agent 作能力基线,校准"成熟长什么样"+ 找差距。**结论是独立设计,不照抄**([memory:feedback_general_platform_positioning](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_general_platform_positioning.md))。三仓实证见 [docs/research/2026-06-06-self-evolving-skills.md](../research/2026-06-06-self-evolving-skills.md) § 10。

> **能力不可弱**([memory:feedback_complete_not_minimal](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_complete_not_minimal.md) / [no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md)):本 Stream 的咽喉是**验证(grounding)**。不允许把"只靠 LLM 自评 / 只靠 prompt 自觉"包装成设计选择 —— 那正是三仓(含当前 helix)共同的缺口,也是学界三篇论文(CoEvoSkills / SPARK / EmbodiSkill)主攻处。

---

## 0. 为什么做(Context)

helix 当前的 skill 是"**人写、版本化、静态启用**"(Stream J.7a + X):skill 由 admin 创作、走 draft→active 审核、agent 在 manifest 里按名引用。但 [目标产品形态](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_target_product_form.md) 是 per-user 持久 agent —— agent 应当**从自己的使用经验里持续长出能力**,而不是永远等人来写 skill。

研究(见上引报告)给出三条硬结论:
1. 自进化的成败系于**验证**:没有 ground truth 时如何确认自生成 skill 真有效。靠模型自评 = 伪进化(在噪声上自我强化 → 误差雪崩、坍缩)。
2. 三仓(openclaw/deer-flow/hermes)在"生成 + 治理"上趋同且成熟,但**全部缺自动效用验证**。
3. helix 独有服务端 trajectory + eval 基建,**天生适合 SPARK 式"后验蒸馏 + 重放验证"** —— 这是把别人做不到的"被证据验证过的自进化"做出来的机会。

**需求决策(用户已确认,需求级)**:
- **范围 = 全闭环**:in-session 自著 + 后验蒸馏 + 重放验证 + 反思归因。
- **治理 = 尽量全自动**:验证通过即上线,仅高危永远人审。
- **验证 = 重放为主**:把蒸馏出的 skill 注入原任务重跑打分。

**预期成果**:开启自进化后,agent 在重复性 / 可复用任务上的成功率随使用上升,且每个自动上线的 skill 都附带可溯的重放验证证据;高危与跨租户始终人审;出现退化能自动回滚。

---

## 1. 范围 & 边界

### 1.1 In-scope(SE-0…SE-9)

| 子项 | 能力 | 当前成熟度 | 本 Stream 交付 | Mini-ADR |
|------|------|-----------|---------------|----------|
| **SE-0** | 总设计 | — | 本文件 + ITERATION-PLAN backlog | — |
| **SE-1** | 数据模型 | 缺失 | 迁移 0065(skill/skill_version 加归属+溯源列 + 新表 `skill_eval_result`);DTO 扩展;`ResourceType` 加 `skill_eval_result`(审计**事件**成员随各自 emit 的 PR 加) | SE-A1 SE-A2 |
| **SE-2** | SkillStore 演化 API | 缺失 | author/refine/fork/promote/record_eval_result + visibility 过滤(base+sql+memory)| SE-A3 |
| **SE-3** | in-session 自著(Layer A，= J.7b-1)| 仅设计 | 4 个 builtin 工具 + provenance + 高危 gate 接线 | SE-A4 |
| **SE-4** | 重放验证 runner(咽喉)| 缺失 | with-vs-without 重放 + judge/assert 打分 → grounding 分 | SE-A5 SE-A6 |
| **SE-5** | 蒸馏 + 归因 | 缺失 | 对比式蒸馏(成功+失败,SkillGen)+ 抽象 guard;失败归因规则前置(Aegis)+ LLM 兜底 | SE-A7 SE-A8 |
| **SE-6** | 进化 worker(Layer B 引擎)| 缺失 | 编排 蒸馏→重放→归因→co-evolve(有界轮)→DRAFT→治理门;wire lifespan | SE-A9 |
| **SE-7** | 全自动治理护栏 | 缺失 | auto-promote 策略 + 速率限制 + 回归回滚 + 熔断 + 审计/指标 | SE-A10 SE-A11 SE-A12 |
| **SE-8** | admin API / UI | 缺失 | review / lineage / eval 证据 / 手动覆盖 / 紧急停 | SE-A13 |
| **SE-9** | self-evolution 基准 + SLO | 缺失 | 证明闭环有效的 eval 数据集 + 性能验收门 | SE-A14 |

### 1.2 Out-of-scope(本 Stream 明确不做)
- **J.7b-2 code 字段执行边界 / J.7b-3 运行期动态加载 / J.7b-5 平台内置 skill 库**:各自独立子项,不在本 Stream(可后续)。本 Stream 自生成 skill 默认**不含可执行 code 字段**;若 agent 声明高危工具,走人审(见 § 6)。
- **跨租户 / 平台 skill 的自动共享**:永远人审,绝不自动(§ 6.2)。
- **重训 / 参数更新**:自进化定义为**不动模型权重**(报告 § 8 理论边界:固定参数有表达力天花板,本 Stream 不挑战它)。

---

## 2. 总体架构:自进化闭环映射到 helix

闭环六环节(报告 § 3)与 helix 组件的映射:

```
  环节            实现                            复用/新建
  (1) 生成   A. in-session: author/refine/fork    SE-3 新工具 + SkillStore(现成)
            B. 后验蒸馏: LLM 从轨迹产草案          SE-5 新 + aux LLM(现成)
  (2) 执行   把 skill 注入 agent 跑原任务          SE-4 replay + graph runner(现成)
  (3) 验证   with-vs-without 打分(grounding)      SE-4 新 + judge(现成) ← 咽喉
  (4) 归因   内容错→改 / 执行·环境错→弃           SE-5 新 + reflect 思路(现成)
  (5) 沉淀   DRAFT skill_version + eval 证据        SE-1/2 + SkillStore/curator(现成)
  (6) 复用   skill_view 装配 + visibility 过滤      SE-2 过滤 + skill_view(现成)
            ↑ 治理门(SE-7): 非高危+达标→自动 active; 高危/跨边界→人审
```

**两层产能源,一个验证+治理门**:
- **Layer A(in-session,对标 hermes/deer-flow `skill_manage`)**:agent 在 run 内主动产/改 skill(SE-3)。交互式、即时。
- **Layer B(后验蒸馏引擎,helix 差异化)**:后台 worker 离线从真实轨迹自动产 skill(SE-6)。数据驱动、规模化。
- 二者产出的 DRAFT **必经同一个验证门(SE-4)+ 治理门(SE-7)**。这是"尽量全自动"安全性的根:**用强重放验证替代人审**,而非取消把关。

> **Mini-ADR SE-A0(架构基线)**:验证门是单一收口。任何路径产生的 skill,要 active 都必须有一条 `skill_eval_result(verdict=pass)` 证据(高危除外 —— 高危即便验证通过也强制人审)。无证据的自生成 skill 只能停在 DRAFT。

---

## 3. 复用清单(现成基建,带锚点)

| 能力 | 锚点 | 复用方式 |
|---|---|---|
| skill 协议/版本/高危/hash | `packages/helix-protocol/src/helix_agent/protocol/skill.py`(`SkillVersion`、`is_high_risk_skill_version`、`compute_content_hash`、`HIGH_RISK_TOOLS`)| 加字段,不改语义 |
| skill 持久化 + curator | `packages/helix-persistence/.../persistence/skill/{base,sql,memory}.py`、`services/control-plane/src/control_plane/skill_curator.py`、迁移 0029/0042/0043/0057 | 加 store 方法 + 新迁移;curator 自动覆盖 agent_private skill |
| skill 装配/lazy/drift/threat | `services/orchestrator/src/orchestrator/tools/skill_view.py`、`make_agent_builder`(`app.py`)| 加 visibility 过滤 + 注册新工具 |
| 后台 worker 骨架 | `services/control-plane/src/control_plane/curation_worker.py`(`start`/`stop`/`_loop` + `_bypass_rls`/`_tenant_scope`)、`memory_consolidator.py` | 克隆为 `skill_evolution_worker.py` |
| curation 候选(蒸馏入口)| `curation_worker.py`(产 `CurationCandidateRecord`)、`protocol/eval_dataset.py`、`persistence/curation/*` | worker B 直接消费 candidate |
| aux LLM 调用 | `services/control-plane/src/control_plane/aux_model_adapter.py`(`LLMRouterAuxModelAdapter`/`make_llm_router_aux_model`)+ `credentials_resolver` | 蒸馏 LLM + 归因 LLM 复用 |
| judge / eval | `tools/eval/_judge.py`(`AnthropicHaikuJudge`/`ScriptedJudge`)、`tools/eval/helix_eval.py`(`EvalCase`/`Assertion`/`run_eval`)| replay 打分复用 |
| 沙箱执行 + 代码审计 | `services/orchestrator/src/orchestrator/tools/sandbox.py`(`ExecPythonTool`/`SupervisorClient`)、`runtime/middleware/sandbox_audit.py` | replay 高危 skill 在沙箱跑 |
| 工具审计 + 高危 gate + 审批 | `graph_builder/builder.py`(`_emit_tool_audit`、approval gate)、`protocol/audit.py`(`TOOL_CALL`/`SKILL_HIGH_RISK_ACTIVATION_BLOCKED`)| 高危人审 + 全程审计 |
| trajectory 记录/读取 | `services/orchestrator/src/orchestrator/trajectory/recorder.py`、`reader.py`(ShareGPT JSONL,按 tenant/outcome/date 分区)| 蒸馏与重放的输入 |

> 结论:helix 已有 ~80% 地基。本 Stream 本质是**补三缺口(自著工具 / 后验蒸馏 / 重放验证)+ 串闭环 + 加全自动护栏**。

---

## 4. 跨切面数据模型(SE-1，纯增量迁移 0065)

> Mini-ADR SE-A1:全部为 additive 列 + 一张新表,无破坏性变更;沿用 0057 的 NULL-tenant RLS 模式(平台 skill `tenant_id IS NULL`)。revision id ≤ 32 字符。

### 4.1 `skill` 表加列(归属 / 血缘,落实 J.7b-1 §15.7)
- `visibility TEXT NOT NULL DEFAULT 'tenant'` — CHECK in (`agent_private`,`tenant`)。agent 自著默认 `agent_private`(仅创建它的 agent 实例可见),促 tenant 需治理门。
- `created_by_user_id UUID NULL` + `created_by_agent_name TEXT NULL` — 自著来源 owner = **per-user 持久 agent** =(tenant, user_id, agent_name),跨 manifest 版本稳定(SE-3a 决策,迁移 0066 改 SE-1 的 created_by_agent_id;provenance 对标 hermes `skill_provenance`)。
- `forked_from UUID NULL` — fork 血缘源 skill_id(对标 GitHub fork)。

### 4.2 `skill_version` 表加列(进化溯源)
- `evolution_origin TEXT NULL` — CHECK in (`in_session`,`distilled`);NULL = 人写(M0 历史行)。
- `distilled_from_trajectory_key TEXT NULL` — Layer B 来源轨迹(可溯到原始证据)。
- `distilled_from_candidate_id UUID NULL` — 来源 `curation_candidate.id`。
- `evolution_round INT NOT NULL DEFAULT 0` — co-evolve 迭代轮次(SE-6)。

### 4.3 新表 `skill_eval_result`(重放验证证据 —— grounding 的可溯账)
```
id UUID PK
tenant_id UUID NULL                 -- 平台 skill = NULL(沿用 0057 RLS)
skill_id UUID NOT NULL
skill_version INT NOT NULL
baseline_score FLOAT NOT NULL       -- 不装 skill 的重放分
skill_score FLOAT NOT NULL          -- 装 skill 的重放分
delta FLOAT NOT NULL                -- skill_score - baseline_score
n_cases INT NOT NULL                -- held-out 重放样本数
replay_source TEXT NOT NULL         -- 'trajectory' | 'eval_dataset'
verdict TEXT NOT NULL               -- CHECK in ('pass','fail','inconclusive')
high_risk BOOL NOT NULL
evolution_round INT NOT NULL DEFAULT 0
created_at TIMESTAMPTZ NOT NULL
-- index (tenant_id, skill_id, skill_version); RLS tenant_id = GUC 或 NULL(平台)
```
这张表是"为什么这个 skill 被自动上线"的唯一可信依据,admin UI(SE-8)直接读它,回归回滚(SE-7d)的**判定基线**也读它(对比 promote 时的 baseline/skill score)。

### 4.4 新表 `skill_run_usage`(上线后归因 —— 回归回滚的成功率信号,SE-7d-1 引入)
```
id UUID PK
tenant_id UUID NULL                 -- 平台 skill = NULL(沿用 0057 RLS)
skill_id UUID NOT NULL
skill_version INT NOT NULL           -- 上线的是具体 version;回滚按 version 判定,不连坐
thread_id UUID NOT NULL              -- 关联 run(可溯 + 去重)
agent_name TEXT NOT NULL             -- 熔断 scope key {tenant}:{agent} 的一半
outcome TEXT NOT NULL                -- 复用 TrajectoryOutcome('success'|'failed'|'max_steps')
created_at TIMESTAMPTZ NOT NULL
-- index (tenant_id, skill_id, skill_version, created_at); RLS tenant_id = GUC 或 NULL(平台)
```
- **为什么专表、不借 trajectory metadata**:回滚判定是 **skill-centric** 查询(给定 skill_version,窗口内它参与的 run 成功率),trajectory 是 **run-centric** 存储 —— 把 skill-centric 查询架在 run-centric blob 全扫上是建模错配,在高吞吐(SkillActivityRecorder 设计目标 1000 runs/sec)下不可持续。专表 `(tenant_id, skill_id, skill_version, created_at)` 索引让滚动窗口聚合 = 范围扫。
- **为什么带 `skill_version`**:promote 的是具体 version,回滚也按 version(下一版可能是人审改好的版本,不该连坐),与 SE 全流程"证据可溯到 version"一致。
- **采集纪律**:`_load_skills` 绑定时已知 skill+version,run 收尾 best-effort 落一行(run 级一次、非 per-step、失败 swallow 不污染 agent 热路径,同 `SkillActivityRecorder` 纪律)。覆盖 build-time 注入 + runtime skill_view 两种绑定路径。

### 4.5 DTO + 审计(`protocol/skill.py` / `protocol/audit.py`)
- `SkillVersion` 加 4 字段(§4.2);`Skill` 加 3 字段(§4.1);新增 `SkillEvalResult`、`SkillVisibility = Literal["agent_private","tenant"]`、`EvolutionOrigin = Literal["in_session","distilled"]`。均 `frozen=True`。
- `audit.py` **双 Literal**(protocol + control-plane 两处,见 [memory:project_audit_literal_drift](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_audit_literal_drift.md)):新增 `SKILL_AUTHORED_BY_AGENT` / `SKILL_REFINED_BY_AGENT` / `SKILL_FORKED` / `SKILL_DISTILLED` / `SKILL_EVOLUTION_VERIFIED` / `SKILL_EVOLUTION_AUTO_PROMOTED` / `SKILL_EVOLUTION_ROLLED_BACK` / `SKILL_EVOLUTION_CIRCUIT_OPEN`;promote 审批复用 §15.7 的 `SKILL_PROMOTE_REQUESTED/APPROVED/REJECTED`。

---

## 5. 子项设计(SE-2 … SE-9)

### SE-2 — SkillStore 演化 API(Mini-ADR SE-A3)
在 `SkillStore` ABC + sql + memory 三处加(签名草案):
- `author_skill_version(*, tenant_id, name, prompt_fragment, tool_names, ..., created_by_agent_id, origin) -> SkillVersion` — 新建 DRAFT + agent_private;复用 `is_high_risk_skill_version` 置 `high_risk`;复用 `compute_content_hash`。
- `refine_skill(*, tenant_id, skill_id, by_agent_id, ...) -> SkillVersion` — 仅可改 `created_by_agent_id == by_agent_id` 的 skill;追加新 version(immutable,沿用现有 add_version)。
- `fork_skill(*, tenant_id, source_skill_id, new_name, by_agent_id) -> Skill` — 复制 source 最新 version 为新 skill v1,`forked_from` 记源(不复用 SkillVersion 行,沿用现状 immutability)。
- `request_promote / approve_promote / reject_promote` — visibility agent_private→tenant 审批流(§15.7);与 status draft→active(U-24 publish gate)**正交**。
- `record_eval_result(SkillEvalResult) -> SkillEvalResult` + `list_eval_results(skill_id, ...)`。
- **visibility 过滤**:`resolve_by_name` / list 在 bind 时按 `(visibility=='tenant') OR (created_by_agent_id == current_agent_id)` 过滤,确保 agent_private 只对本 agent 可见。

> 权限矩阵(§15.7.3)照搬:自己 authored → refine/archive/promote;他人/admin/human skill → 仅 fork;提权(delete/pin/publish-active)→ admin-only(agent 永不能自己把 skill 设 active,只能请求)。

### SE-3 — in-session 自著工具(Layer A，Mini-ADR SE-A4)
新文件 `services/orchestrator/src/orchestrator/tools/skill_authoring.py`,4 个 builtin(对标 hermes/deer-flow `skill_manage`,但拆成语义清晰的 4 个,符合 §15.7):
- `author_skill(name, description, prompt_fragment, tool_names) ` → SkillStore.author_skill_version(DRAFT/agent_private)。
- `refine_skill(skill_ref, patch...)` → refine_skill。
- `fork_skill(source_skill_ref, new_name)` → fork_skill。
- `propose_skill_to_tenant(skill_ref, reason)` → request_promote(发起人审,不直接生效)。

接线:在 `KNOWN_BUILTINS`(`tools/assembly.py`)注册;经 `make_agent_builder` 注入 `created_by_agent_id` 上下文(provenance);复用 U-22 threat scan(写时)+ U-24 高危 gate(active 时);每个动作 emit 对应 AuditAction;**速率限制**(SE-7)前置。默认产出即进验证门(SE-4/6),不直接 active。

### SE-4 — 重放验证 runner(咽喉，Mini-ADR SE-A5 / SE-A6)
拆三 PR(`services/orchestrator/src/orchestrator/evolution/`):**SE-4a** `grounding.py`(判定大脑,纯逻辑)+ **SE-4b** `replay.py`(编排核心,接缝解耦,CI 全测)+ **SE-4c** `graph_runner.py`(`GraphReplayTaskRunner` —— `TaskRunner` 真实现:`from_candidate` 构造两个仅差 `spec.skills` 的 AgentSpec、注入式 builder→`graph.ainvoke`→取末条 AIMessage、两变体各 build 一次缓存;高危沙箱由 builder 接 sandboxed ToolEnv 天然满足。wiring 用 fake CI 单测,**真 LLM e2e 归 SE-9 基准/eval harness**——CI integration 无 model key)。
- 输入:一个候选 skill(DRAFT)+ 一组 held-out 任务(来源:① 同类 trajectory 的初始 user 消息;② `eval_dataset` 该 agent 的 golden/regression 案例)。
- 过程:对每个任务跑两遍 agent graph —— **baseline(不装该 skill)** vs **treatment(装该 skill)**,judge(Haiku,temp=0)+ assertions 各打分。真 graph 与真 judge 经 `TaskRunner`/`ReplayJudge` 两个 Protocol 接缝注入(SE-4c 提供真实现;CI 用 fake runner + scripted/marker judge)。
- 输出:`SkillEvalResult`(baseline_score / skill_score / delta / n_cases / verdict)。
  - **Mini-ADR SE-A5(grounding 判定,经外部检索强化为配对显著性)**:`verdict=pass` 要求(a)配对显著性检验 p < α(默认 α=0.05;二元结果 McNemar、连续/序数分 Wilcoxon 符号秩)、(b)效应量 `delta ≥ θ_delta`(默认 0.08 ≈ 8pp,对齐 n≈100 时 95% CI 半宽经验)、(c)`n_cases ≥ N_min`(默认 **6** —— 双侧精确 p 在 n=5 下限 0.0625>0.05,6 才是 α=0.05 的显著性下限;T≥5 仅稳定性指南)、(d)treatment 无新增失败;任一不满足 → `inconclusive`(落 DRAFT 待人审),execution/environment 错则归 `inconclusive` 不归 `fail`。判定依据见下「SE-4 设计依据」。
  - **Mini-ADR SE-A6(确定性 / 成本 / 打分形态)**:CI 用 `ScriptedJudge`/fake runner(确定性,`-m "not integration"`);真 Haiku judge + 真 graph 重放只在 integration。高危 skill 的重放在 gVisor 沙箱内(复用 ExecPythonTool 路径,SE-4c)。**打分用逐点(pointwise)**:baseline/treatment 各自独立打分 → 天然无位置偏置,不需 swap-order(swap-order 只在 pairwise A/B judge 才需)。
- 防泄漏:held-out 任务不得与蒸馏来源轨迹同一条(SPARK 的 held-out 精神),`replay.py` 在重放前剔除 `trajectory_key==distilled_from_trajectory_key` 的任务,避免"对着训练样本刷分"。

#### SE-4 设计依据:三问题 × 论文启示 × 外部工程实践

> SE-4 是咽喉。本节把"为什么这样验证"落到可追溯的依据上 —— 三篇核心论文回答了三个必答问题(**用什么判 / 判的依据 / 判错怎么办**),并各配一轮外部检索(2026-06,见文末 Sources),确认别人已有的工程实现方法,据此强化 SE-A5/SE-A6。

**前置事实(SE-4 必须存在的最强外部背书)**:SkillsBench(86 任务 / 11 域 / 确定性 verifier / 7 配置 / 7308 轨迹)实测 **自生成 skill 平均比 skill-free 基线低 1.3pp,5 个配置里仅 1 个有改善**。结论:零验证的自生成会**劣化**性能;自生成的可行性取决于"域特异性 + 自动验证可得性"。这正是 SE-A0(无 pass 证据只能停 DRAFT)的实证根据 —— **没有 SE-4,自进化大概率是负收益**。

**问题 1 —— 用什么判(验证器形态)**
- 论文(CoEvoSkills):验证器要随生成器协同进化,标量分只在**相对基线**下才有意义。
- 外部实现:
  - **CTA(Counterfactual Trace Auditing)**:同任务跑 with-skill vs without-skill 两条轨迹,切成目标导向阶段(Orientation/Implementation/Validation/Debugging/Finalization,确定性 FSM),用 DTW 对齐阶段 + 在 reasoning 文本上做 intent 级 TF-IDF 余弦对齐,对每个分歧 emit 一条 Skill Influence Pattern(SIP)。**关键教训**:pass-rate 会饱和、且 skill 的有益/有害效应会在终态相互抵消(其 SWE-Skills-Bench 49 任务上聚合仅 +0.3pp);只看 pass-rate 会漏判。
  - **Voyager**:双通道验证 = LLM self-verifier + 客观环境反馈(物品是否到手),迭代最多 4 轮(环境态 / 执行错 / 自检),**只有真成功才入库**。
  - **swap-order 配对 judge(可直接落地的方法)**:pairwise LLM-judge 有 60–75% 的位置偏置(偏向第一个)。工程做法 = **同一对 baseline/treatment 答案跑两次、互换位置、聚合**;两次一致才算"位置一致",不一致计半胜/平局(three-option A/B/C-tie 模式)。代价是每次评估 ≥2 次 judge 调用。
- **对 SE-4 的强化**:① with-vs-without 对照保留(已定);② **新增轨迹级 SIP 作为二级信号**:当 baseline/treatment 终态打分接近(|delta| < θ_delta)时,落到轨迹 diff 看 skill 是否在中间阶段产生有益偏移(读/写/搜索/执行的差异),避免被饱和 pass-rate 误判为"无效";③ **judge 用 swap-order 配对法消位置偏**(treatment vs baseline 互换两跑),分层可替换不变(CI scripted / integration Haiku)。

**问题 2 —— 判的依据(后验证据 + 统计有效性)**
- 论文(SPARK/PDI):只信环境后验证据(任务过没过 / assert 红绿 / reward),不信模型自评;held-out 分离。
- 外部实现:
  - **执行式验证(grounding 的"硬"来源,可直接落地)**:Reflexion / Self-Refine / Self-Debug 的统一做法 —— 不靠模型自夸,而是**跑出来**:生成产物 → 在环境/自生成单测里执行 → 把执行错与环境反馈喂回 → 迭代至停止条件(LangGraph 里就是一条 conditional edge 回环)。编程类任务能 self-generated unit test → 直接 pass@1。对 SE-4 的含义:**能 assert 的任务一律走执行式后验(最硬信号),judge 仅作无法 assert 时的退路**。
  - 统计严谨性(原 SE-A5 的薄弱处):配对二元结果用 **McNemar 检验**;配对连续/序数分用 **Wilcoxon 符号秩检验**,出 p 值而非只看 delta 均值。
  - 样本量现实:n=100 二元试验时 95% CI 半宽约 7–9.5pp(成功率 0.3–0.8),**差异 < ~8–10pp 须谨慎**;**T≥5 次重复**即可显著稳住估计(ICC 研究亦支持报置信区间)。
- **对 SE-4 的强化(改写 SE-A5)**:grounding 判定从"裸 delta ≥ θ"升级为 **配对显著性检验**:`verdict=pass` 要求(a)配对检验 p < α(默认 α=0.05,二元用 McNemar、连续用 Wilcoxon),(b)效应量 `delta ≥ θ_delta`(默认 θ_delta=0.08,即 ~8pp,对齐 CI 经验),(c)`n_cases ≥ N_min`(默认 N_min=5,对齐 T≥5),(d)treatment 无新增失败。任一不满足 → `inconclusive`(落 DRAFT 待人审)。`skill_eval_result` 增记 p 值与检验类型(写入既有 `replay_source`/扩展字段或证据 JSON,SE-1 表已可承载,必要时 SE-7 细化补列)。

**问题 3 —— 判错怎么办(失败归因)**
- 论文(EmbodiSkill):skill 没生效 ≠ skill 坏;分 skill-content-error vs execution-lapse。
- 外部实现(三个可跑算法 + 一条关键准确率天花板):
  - **ICML 2025 Spotlight《Which Agent Causes Task Failures and When?》**(`github.com/mingyin1/Agents_Failure_Attribution`)给了 3 个可直接复用的归因算法,带可运行入口(`python inference.py --method step_by_step --model ...`):
    - **All-at-Once**:整条失败日志一次过,LLM 直接点名责任方。最省 token,粒度最粗。
    - **Binary Search**:把日志对半切、迭代收窄定位失败步。token 与精度折中。
    - **Step-by-Step**:逐步串行判定每一步对错。最贵,理论最细。
  - **关键天花板(直接改变我们的设计取舍)**:即便最优方法,**定位"责任 agent" 仅 53.5%、定位"失败步" 仅 14.2%**,o1/R1 等顶级推理模型也达不到实用 → **自动化的细粒度步级归因不可信,不能拿它当门控**。
  - 业界失败 taxonomy 与 EmbodiSkill 同构:**execution error**(早崩 / 环境装配失败 / 没走到产出)、**skill content error**(领域知识缺口 / 逻辑错)、**tool/environment failure**(依赖装不上 / 权限 / 配置)。
  - **动态归因 / 反事实探针**:对候选失败步做**受控重执行**(DoVer 的 intervention-driven、AgentRx 的轨迹诊断)过滤伪候选;AgentNoiseBench 证明工具噪声会显著拉低成功率 → 佐证须容忍瞬时抖动。
- **对 SE-4 的强化**:① replay 失败时**必须留存原始失败信号**(错误类型 / 退出阶段 / 工具负面断言),作为 SE-5 归因(SE-A8)的输入,不能只回一个 `fail`;② **归因只做粗粒度二分类(内容错 vs 执行/环境错),不做自动步级定位**(因 14.2% 步级准确率不可信)—— 用 All-at-Once 量级的成本,辅以失败信号 taxonomy 规则(环境/工具型直接归执行错),不上昂贵的 Step-by-Step;③ verdict 三态中 **execution/environment error 一律归 `inconclusive` 而非 `fail`**,绝不喂回自进化(防在噪声上自我强化、防坍缩);④ 高危候选沙箱重放(已定),收敛 execution-lapse 来源。

**问题 4(补查)—— 无预言机 / 开放式任务能否可信地自动验证?**

> 上面三问的方法,威力几乎都来自"环境自带 ground-truth 预言机"(Minecraft 物品 / 单测 / deterministic verifier)。但 helix 头号形态是 per-user 持久 agent 做开放式业务/知识工作,**大面积任务无硬 verifier**。这片是 SE-4 最大的未验证风险,故补查一轮"无预言机下的可信验证"。

- **风险被实证证实(不是臆测)**:self-rewarding / intrinsic reward 训练会导致 **reward hacking 与模型坍缩**,最优策略退化成"无论输入都吐同一答案";intrinsic reward 通常**无法超越基于真值的 reward**(SRT / DARL)。→ 坐实了对 CoEvoSkills 共进化"无外部锚→漂移"的担忧。
- **破局量化结论(最值钱)**:哪怕只在 **1% 的评估样本中混入可验证真值**,就足以**大幅压制 reward-hacking**(1% 已显著,10% 更稳)。→ 不必每任务都有预言机,**每轮验证掺一小撮可验证锚点即可钉住 judge/verifier**。
- **无预言机的实际方法族**:① **Generative Reward Model**(UI-TARS-2 的 ORM 对整条 trajectory 产标量分;pairwise GenRM 对固定参考产胜出置信度);② **rubric-guided verification**(开放式研究 agent,2601.15808);③ 可靠性靠**校准**——用人工修正建 few-shot、**持续追踪 judge 与人的一致率**;④ `Audited Skill-Graph Self-Improvement`(2512.23760)印证:skill 自进化须**锚在 verifiable rewards**。
- **关键限定(防过度乐观)**:这些方法的验证强度是给 "RL reward 信号 / eval proxy" 用的,**无人证明其强到可当"多租户线上自动上线门"**——上线门可靠性要求更高。故无预言机 skill 的全自动仍比上述论文用例更冒险。
- **对 SE-4/SE-7 的强化(新 Mini-ADR SE-A5b:grounding 信号强度三级分流)**:auto-promote 前置加"信号强度"维度——
  - **T1 有硬 verifier**(assert/确定性/golden):执行式验证 + 配对统计 → **全自动**(迁移已验证)。
  - **T2 无硬 verifier 但有校准 rubric-GenRM**:要求(a)replay 集内**掺入的可验证锚点全过**、(b)GenRM 置信高、(c)judge 与人**追踪一致率 ≥ 阈** → **限定自动**(三条全满足)。
  - **T3 无 verifier、无校准锚点**:→ **人审**(无方法证明可自动)。
- **对 SE-6 的强化(给 SE-A9 共进化环补防坍缩)**:**每轮验证强制掺 1%–10% 可验证锚点任务**(量化背书),锚点一旦回归即判该轮无效 —— 把"生成/验证器分离"从口号变成有外部锚的硬约束。

> Sources(2026-06 检索)—— 方法级在前,结果/背书在后:
> **问题1(判)**:[CTA — Counterfactual Trace Auditing](https://arxiv.org/abs/2605.11946) ·
> [Voyager(双通道验证)](https://arxiv.org/abs/2305.16291) ·
> [位置偏置 + swap-order 配对 judge](https://arxiv.org/html/2406.07791v9)。
> **问题2(依据)**:[Reflexion(执行式验证回环)](https://openreview.net/pdf?id=vAElhFcKW6) ·
> [Self-Refine](https://openreview.net/pdf?id=S37hOerQLB) ·
> [Stochasticity in Agentic Evaluations(ICC/置信区间)](https://arxiv.org/pdf/2512.06710)。
> **问题3(归因)**:[Which Agent Causes Task Failures and When?(ICML'25 Spotlight,3 算法+准确率天花板)](https://ag2ai.github.io/Agents_Failure_Attribution/)([code](https://github.com/mingyin1/Agents_Failure_Attribution)) ·
> [DoVer(反事实受控重执行)](https://arxiv.org/pdf/2512.06749) ·
> [AgentNoiseBench](https://arxiv.org/pdf/2602.11348)。
> **问题4(无预言机/防坍缩)**:[1% 锚点压制 reward-hacking(RL-from-Meta-Evaluation)](https://arxiv.org/pdf/2601.21268) ·
> [LLMs Gaming Verifiers / RLVR reward hacking](https://arxiv.org/pdf/2604.15149) ·
> [SRT: Can LRMs Self-Train?(自奖励坍缩)](https://self-rewarding-llm-training.github.io/) ·
> [UI-TARS-2(generative ORM)](https://arxiv.org/pdf/2509.02544) ·
> [Inference-Time Scaling of Verification(rubric-guided)](https://arxiv.org/html/2601.15808v1) ·
> [Audited Skill-Graph Self-Improvement](https://arxiv.org/pdf/2512.23760)。
> **结果背书/综述**:[SkillsBench(零验证 −1.3pp)](https://arxiv.org/html/2602.12670v1) ·
> [SoK: Agentic Skills](https://arxiv.org/html/2602.20867v1)。

### SE-5 — 蒸馏 + 归因(Mini-ADR SE-A7 / SE-A8)
拆两 PR:**SE-5a** `skill_distiller.py`(蒸馏)+ **SE-5b** `skill_attribution.py`(归因)。aux LLM 均经接缝注入(CI fake / 真 aux LLM 接线推到 SE-6 worker)。

- **蒸馏(SE-5a,SE-A7)**:输入一条 `curation_candidate` + 其轨迹(`TrajectoryReader.read(trajectory_key).messages`),产出结构化 `SkillDraft`(name / prompt_fragment / tool_names / description / category / high_risk),**不落库**(落 DRAFT 是 SE-6 的活)。
  - **对比式蒸馏(contrastive induction,经检索升级,SkillGen)**:不只学 `positive_feedback` 成功打法,而是**对比成功 vs 失败轨迹** —— 抽"成功流程 + 复发失败模式 + 在邻近成功里出现却在失败里缺失的行为",失败模式编成 prompt_fragment 里的 guard("别这么做")。比原 positive-only 强一档([memory:feedback_complete_not_minimal] 能力不可弱)。
  - **抽象 guard(经检索补,防过度具体)**:蒸馏 prompt 强制 **type-level 抽象**(抽规律 / 前提条件,不抄轨迹原文);校验**拒绝含轨迹原值 / ID / 时间戳的草案**(否则退化成记忆碎片,失通用性)。
  - **只蒸馏后验证据**(SPARK):只从真实交互蒸馏,不从先验计划编;`tool_names ⊆ 轨迹实际用过的工具`(防 LLM 瞎加工具)。
- **归因(SE-5b,SE-A8,EmbodiSkill)**:当重放失败,判「**skill 内容错**」(→ co-evolve 修订)还是「**执行 / 环境错**」(→ 丢弃,不喂回)。**两阶段混合(经检索验证 = 业界主流分法)**:
  - **① 规则前置(程序化,SkillsBench 式)**:失败信号命中**环境失败 taxonomy(锚 Aegis 6 模式)**+ hermes 不捕获清单(环境依赖 / 工具负面断言 / 一次性瞬时错 / 沙箱·网络·超时)→ 直接判执行/环境错,不调 LLM。
  - **② LLM 兜底(LLM-as-judge,Terminal-Bench 式)**:规则判不了时,All-at-Once 喂失败轨迹 + skill 内容问一个粗问题(内容 vs 执行)。**保守默认**:不确定 → 执行错(不学,偏向防坍缩)。
  - **取舍**:只做粗粒度二分类,**不做自动步级定位**(步级准确率仅 14.2% 不可信);不上 Step-by-Step。

#### SE-5 设计依据:对比检索(2026-06)
> 与 SE-4 同纪律,蒸馏/归因方法各配一轮检索验证(见文末 Sources)。结论:**整体架构被 SkillGen 近乎 1:1 印证(SOTA 形态)**,但原蒸馏方法偏弱,据检索升级。

- **架构印证 —— SkillGen(2605.10999)**:把 skill 合成建模为 **intervention problem**(= helix baseline-vs-treatment),pipeline = **contrastive induction → generation-verification-refinement loop → 按 held-out 净效应选 skill(计入修复与回归)**,产出单个**可审计** skill。这与 SE-4(净效应验证)+ SE-5(轨迹蒸馏)+ SE-6(gen-verify-refine 环)几乎重合 → 验证 helix 路线是 SOTA。
- **蒸馏强化 1(对比式)**:SkillGen 从成功+失败双向对比蒸馏,强于原 positive-only → 升级(见上)。
- **蒸馏强化 2(抽象)**:多源点名"解决单实例的轨迹须抽象成处理整类的 skill",否则退化记忆碎片;缓解 = type-level 抽象 → 加 prompt 约束 + 草案校验(见上)。
- **归因验证(混合法)**:SkillsBench = 程序化分析结构化输出(= 规则前置);Terminal-Bench = LLM-as-judge 分类轨迹(= LLM 兜底)。两阶段正是两大基准的分法。
- **环境 taxonomy 锚点 —— Aegis(2508.19504)**:agent-environment 失败 6 模式(142 轨迹 / 3656 turn 实证)→ 替代 ad-hoc 不捕获清单,给"执行/环境错"一个有据的判定基。

> Sources(2026-06):
> **蒸馏**:[SkillGen(intervention + contrastive + 净效应验证)](https://arxiv.org/abs/2605.10999) ·
> [Trace2Skill](https://arxiv.org/html/2603.25158v1) ·
> [ExpeL / AWM(经验→可复用)](https://arxiv.org/html/2604.08224v1) ·
> [SkillWeaver(web agent 自发现+打磨 skill)](https://arxiv.org/pdf/2504.07079) ·
> [SkillGenBench](https://arxiv.org/html/2605.18693)。
> **抽象/过拟合**:[Structured Agent Distillation(span 级监督防 token 模仿)](https://arxiv.org/pdf/2505.13820) ·
> [MemSkill(type-level 抽象)](https://arxiv.org/html/2602.02474)。
> **归因**:[Aegis(agent-environment 6 模式 taxonomy)](https://arxiv.org/abs/2508.19504) ·
> [Which Agent Causes Task Failures(14.2% 步级天花板)](https://arxiv.org/abs/2505.00212) ·
> [SkillsBench](https://arxiv.org/pdf/2602.12670)。

### SE-6 — 进化 worker(Layer B 引擎，Mini-ADR SE-A9)
新文件 `control_plane/skill_evolution_worker.py`,克隆 `CurationWorker` 骨架(`start`/`stop`/`_loop`/`_bypass_rls`/`_tenant_scope`),`run_once` 编排:
1. 扫 `curation_candidate`(status=pending,signal∈{positive_feedback, failed_outcome})。
2. 蒸馏草案(SE-5)。
3. **co-evolve 有界轮**(SE-A9,CoEvoSkills 精神):重放验证(SE-4)→ 若 fail 且归因为内容错(SE-5)→ 让 LLM 据 grounding 反馈修订草案 → 再验证;最多 `R_max` 轮(默认可配)。验证器(judge)与生成器(蒸馏 LLM)分离,避免自评刷分。
4. 落 DRAFT skill_version(`evolution_origin='distilled'` + 溯源列)+ 写 `skill_eval_result`。
5. 交治理门(SE-7)。
wire 进 `app.py` lifespan(同 CurationWorker/MemoryConsolidator;单副本;aux LLM 缺凭证时降级为 no-op 不启动,沿用 memory consolidator 的 fallback 模式)。

### SE-7 — 全自动治理护栏(Mini-ADR SE-A10/A11/A12)
`control_plane/skill_evolution_policy.py`:
- **auto-promote 策略(SE-A10)**:`verdict=pass` 且 `not high_risk` 且目标 visibility 在边界内 → 自动 set_status active(agent_private 直接 active;agent_private→tenant 需达标的同时仍走 request/approve,除非配置允许 tenant 内自动)。`high_risk` → 永远人审(U-24)。跨租户/平台 → 永远人审。`inconclusive`/`fail` → 停 DRAFT。
- **回归回滚(SE-A11,拆 SE-7d-1/2/3)**:promote 后按 **专表 `skill_run_usage`(§4.4)** 归因该 skill_version 关联 run 的 outcome,滚动窗口内成功率显著下降 → 自动 archive(对标报告 § 7 防坍缩)。**按 version 判定不连坐**。
  - **SE-7d-1 归因采集**:`_load_skills` 绑定时落 `skill_run_usage` 一行(run 收尾 best-effort,run 级一次、失败 swallow,同 `SkillActivityRecorder` 纪律)。迁移 + DTO + store 写/聚合方法。
  - **SE-7d-2 判定器**(纯逻辑,CI 可测):`decide_rollback(outcomes, *, promote_baseline, config) -> RollbackDecision`。**统计修正**:回滚**不是配对场景**(无 case 级 with-vs-without 配对),不能复用 SE-4a 的 McNemar/Wilcoxon;正确检验是**单侧精确二项**(窗口成功数 ~ Binomial(n, baseline);成功率显著低于 baseline 即 `p<α` ∧ `drop≥θ`)+ **绝对地板**(窗口率 < floor 即回滚,兜底"在弱 baseline 上 promote 又退化到净有害");`cancelled` 不计入样本(用户取消非 skill 之过);`n < n_min` → 不动(防小样本误杀)。二项 CDF 手写无 scipy(同 SE-4a)。
  - **SE-7d-3 monitor + 动作**:拆 SE-7d-3a(gate,CI 可测)+ SE-7d-3b(monitor 循环 + 埋点 + 接线,真路径)。
    - **SE-7d-3a `RollbackGate`**(对称 SE-7c `PromotionGate`):给一个 live version + promote 基线 → 聚合 `skill_run_usage` 窗口 → `decide_rollback` → ROLLBACK 则 `set_status(ARCHIVED)` + `breaker.record(ok=False)`(同 `{tenant}:{agent}` scope,回滚=失败 promote,坏 skill 累积自动熔断全自动通道)+ emit `SKILL_EVOLUTION_ROLLED_BACK`。**证据落 audit details**(rate/baseline/drop/p/n)**而非 `skill_eval_result`**——回滚不是 replay,`replay_source` CHECK 容不下,审计行才是其正确归宿(设计修正)。
    - **SE-7d-3b-i `RollbackMonitor` + 接线**(纯 control-plane;sweep 逻辑 CI 可测、bypass GUC + lifespan 真路径):周期 `run_once` 跨租户(owner RLS 豁免,同 curator,skill 表 ENABLE-only)枚举 ACTIVE distilled version + 取 promote 基线(最近 pass `skill_eval_result.skill_score`,无则 skip)→ 逐个过 `RollbackGate`。**共享熔断器**:app lifespan 构造一个 `CircuitBreaker` 注入 PromotionGate(SE-7c)+ RollbackGate —— 回滚喂 `ok=False` 才真能熔断 auto-promote 通道(否则死效果);`build_evolution_worker` 加可选 `breaker` 参。settings 门 `enable_skill_rollback_monitor`(默认 off)。**埋点未上线前是安全 no-op**(没数据不动)。
    - **SE-7d-3b-ii 埋点**(把数据喂进 monitor)。**调研修正**:主 run 路径上 trajectory 录制其实也没接(`run_agent` 3 调用点都不传 `trajectory_recorder`,sse.py `_dispatch_trajectory` 在主 run dead),故不能"复用现成 seam",而是**照搬 `SkillActivityRecorder`/`trajectory_recorder` 成熟模式做同构兄弟**。拆 ii-A(基础,CI 可测、零热路径改动)+ ii-B(emit + 接线,真路径 integration):
      - **SE-7d-3b-ii-A 基础**:helix-common `SkillRunUsageRecorder` Protocol + `BoundDistilledSkill` DTO(单向依赖,同 `skill_activity.py`,`outcome: str` 避免 common→protocol);`BuiltAgent.bound_distilled_skills` ← 纯 helper `_bound_distilled_skills(resolved_versions, agent_name)`(distilled+tenant 子集,确定序);control-plane `StoreSkillRunUsageRecorder` 实现(写 `SkillStore.record_skill_run_usage`,best-effort swallow)。
      - **SE-7d-3b-ii-B emit + 接线**(真路径):`RunRecord.bound_distilled_skills` 字段;`run_agent` 加 `skill_run_usage_recorder` 参 + `_dispatch_skill_run_usage` fire-and-forget(create_task+硬超时+swallow,绝不阻塞 run 热路径);sse.py 4 个终态分支(success/failed/max_steps/cancelled)紧挨 trajectory 处 emit 每个 bound 版本;`AgentRuntime.skill_run_usage_recorder` 注入,3 调用点(api/runs trigger+resume、trigger_firing)建 record 时填 `built.bound_distilled_skills` + 传 recorder。**emission 与 monitor 同 `enable_skill_rollback_monitor` 门**(一个 flag 控整条 SE-7d 数据路;flip on 后窗口 fill 满才回滚,保守可接受)。
- **速率 / 熔断(SE-A12)**:per-agent / per-tenant 每小时自著 + 自动 promote 上限;自动通道异常率超阈值 → 熔断(`SKILL_EVOLUTION_CIRCUIT_OPEN`),降级为全人审直到人工复位。
- 内容安全复用:U-22 threat scan + content_hash drift + 沙箱。全程 AuditAction + Prometheus 指标(`helix_skill_evolution_*`)。

### SE-8 — admin API / UI(Mini-ADR SE-A13 / SE-A13b / SE-A13c / SE-A13d)

> **SE-8-0 细化设计(2026-06-08)**:把 SE-1…SE-7 已闭环的自进化后端暴露给运营人审。用户拍板 4 项需求级决策:① promote 审批做**完整流**(request/approve/reject + 审计 + 状态机);② 紧急停做**持久 kill-switch**(非进程内);③ 可视化用**手工 SVG + dagre**(否决 Recharts,贴 Stream H "不堆组件库 / 避免通用审美"基线);④ **IA 不新建页 / 不加导航**——丰富 Skills 列表 + SkillDetail 展开全貌 + 列表头 kill-switch 开关(用户心智:列表 OK 但信息匮乏,完整信息进 detail)。**权限统一规则**:租户管理员操作**本租户内**,系统管理员操作**所有租户**(promote 审批 + 紧急停 + 回滚同此规则)。

#### SE-8 IA(前端形态,Mini-ADR SE-A13d)
落 [admin UI 设计基线](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/project_admin_ui_design_baseline.md);接线点清单见 [memory:admin_ui_wiring_touchpoints](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/reference_admin_ui_wiring_touchpoints.md)。**不新建路由 / 不加侧导航项**:
- **Skills 列表(`SkillsList.tsx` 丰富,= review 队列)**:现有列太薄(仅 Name/Status/Category/Description/Updated)。补:**生效版本 vs 最新版本**(`live v2 · latest v4` —— 自进化下蒸馏草案版本常 ≠ 线上 active 版本,否则看不出"有新版本待审")、**可见性**(agent_private/tenant)、**来源**(人写/in_session/distilled)、**owner**(自著 agent)、**最近验证**(verdict+delta 如 `pass +0.12`)、**promote 状态**(待审/无)。新筛选:可见性 / 来源 / **"仅待审 promote"**(勾上即 review 队列,不单独建队列页)。
- **SkillDetail(`SkillDetail.tsx` 展开全貌)**:版本列表(已有)+ 每版 **eval 证据**(baseline-vs-skill delta 手工 SVG 配对条 + p值/n_cases)+ **lineage 血缘**(forked_from / distilled_from 链,dagre 布局 + 自定义 SVG 节点)+ **provenance**(自著 agent/user、蒸馏来源轨迹)+ **操作区**:approve / reject promote、**archive(= 停单个 skill)**。
- **kill-switch(Skills 列表页头部,scope 感知开关)**:租户管理员见本租户开关;系统管理员见全局 + 按租户。旁显熔断状态(in-process breaker 自动熔断也在此可见)。**与 archive 正交**:archive 停一个已有 skill;kill-switch 停"自动造/上线**新** skill"的整条流水线(不动已有 skill)。

#### SE-8 数据模型(新迁移 0068,纯增量;沿用 0057 NULL-tenant RLS;revision id ≤ 32 字符)
- **新表 `skill_promote_request`(Mini-ADR SE-A13b,promote 审批流)**——与 status(draft→active)**正交**,承载 agent_private→tenant 可见性审批:
  ```
  id UUID PK
  tenant_id UUID NOT NULL              -- agent_private→tenant 恒在租户内;ENABLE RLS tenant_id=GUC
  skill_id UUID NOT NULL
  skill_version INT NOT NULL           -- 申请提升的具体版本(与 SE 全流程"证据可溯到 version"一致)
  status TEXT NOT NULL                 -- CHECK in (pending/approved/rejected/superseded)
  requested_by_user_id UUID NULL       -- agent 经 propose_skill_to_tenant 发起则记 owner;admin 代发起可空
  requested_by_agent_name TEXT NULL
  reason TEXT NOT NULL DEFAULT ''
  decided_by_user_id UUID NULL         -- 审批人(租户 admin 或 system_admin)
  decided_at TIMESTAMPTZ NULL
  decision_reason TEXT NOT NULL DEFAULT ''
  created_at TIMESTAMPTZ NOT NULL
  -- index (tenant_id, status, created_at); 同 skill 仅一条 pending(部分唯一索引 where status='pending')
  ```
  - **为何专表非 status 字段**:要可查"待审队列"+ 决策审计 + 多次申请历史;status 机已被 draft/active/stale/archived 占满,可见性是独立维度(§5 SE-2 已定"正交")。
  - **跨租户读**:system_admin review 队列用 `list_promote_requests_all_tenants`(对标 `curation.list_for_review_all_tenants`);RLS ENABLE-only + tenant GUC,跨租户读经 `ensure_tenant_scope`+CrossTenant(skill 生态 ENABLE-only,**不加 FORCE**,见 [memory:skill_curator_owner_rls_exemption])。**仅 integration 真 PG 验**。
- **新表 `skill_evolution_kill_switch`(Mini-ADR SE-A13c,持久紧急停)**——补 in-process `CircuitBreaker`(`skill_evolution_limits.py`,per-worker、重启即丢、多副本不一致)的持久化缺口:
  ```
  id UUID PK
  scope TEXT NOT NULL                  -- CHECK in (global/tenant)
  tenant_id UUID NULL                  -- global=NULL(平台行,沿用 0057;租户读也要 bypass);tenant=该租户
  engaged BOOL NOT NULL
  reason TEXT NOT NULL DEFAULT ''
  engaged_by_user_id UUID NULL  engaged_at TIMESTAMPTZ NULL
  released_by_user_id UUID NULL  released_at TIMESTAMPTZ NULL
  updated_at TIMESTAMPTZ NOT NULL
  -- 部分唯一索引:global 一行(where scope='global')、每租户一行(where scope='tenant', tenant_id)
  ```
  - **接线**:`decide_promotion`(SE-7a)新增输入 `evolution_halted: bool`(与现有 `breaker_open` 并列)→ true 则 HUMAN_REVIEW(降级全人审);gate 在判定前 `is_evolution_halted(tenant_id)`(global OR 该 tenant)。**与 in-process breaker 互补**:breaker=自动(失败率超阈自熔断),kill-switch=人工持久总闸。
- **DTO(`protocol/skill.py`)**:新增 `SkillPromoteRequest`、`PromoteRequestStatus = Literal[...]`、`KillSwitch`、`KillSwitchScope = Literal["global","tenant"]`,均 `frozen=True`。
- **审计(双 Literal,protocol + control-plane,见 [memory:audit_literal_drift])**:`SKILL_PROMOTE_REQUESTED` / `SKILL_PROMOTE_APPROVED` / `SKILL_PROMOTE_REJECTED`(§4.5 已预告)+ `SKILL_EVOLUTION_KILL_SWITCH_ENGAGED` / `SKILL_EVOLUTION_KILL_SWITCH_RELEASED`;`ResourceType` 加 `skill_promote_request` / `skill_evolution_kill_switch`。

#### SE-8 Store 方法(SkillStore base+sql+memory)
- **list_skills 扩展**:加 `visibility` / `evolution_origin` / `created_by_user_id` 过滤参;列表需带 live-version + 最近 verdict/delta + pending 标志 → 设计为**批量旁路查询**(列表主查 skill,再按 id 批量取 `skill_eval_result` 最近一条 + `skill_promote_request` pending),不在主查塞重 JOIN(避免高吞吐退化)。
- **promote 审批**:`request_skill_promote` / `approve_skill_promote`(置 status=approved + `set_visibility(tenant)`)/ `reject_skill_promote` / `list_promote_requests(+_all_tenants)`。approve 同时把 skill `visibility` agent_private→tenant(原子)。
- **kill-switch**:`get_kill_switch(scope, tenant_id)` / `set_kill_switch(...)` / `is_evolution_halted(tenant_id) -> bool`(global OR tenant)。
- 复用现成:`list_eval_results`(eval 证据)、`get_skill`+`get_version_by_number`(lineage 字段)、`set_status(ARCHIVED)`(archive)。

#### SE-8 API(control-plane,全 raw JSONResponse + audit_emit;authz 租户 admin 管本租户 / system_admin 跨租户)
样板 `api/{skills,curation,platform_skills,audit}.py`;envelope 对账见 [memory:envelope-vs-raw](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_envelope_vs_raw_contract_check.md)(skill 端点返 raw,SDK 用 `apiClient` 直取不 `getJson`)。
- **实现修正(SE-8-2)**:除 `GET /v1/skills`(原地扩展 visibility/created_by_user_id 过滤 + `_skill_dict`/`_version_dict` 补 SE 字段)外,所有 SE-8 治理端点放**独立 router `api/skill_evolution.py`(prefix `/v1/skill-evolution`)**——因 `/v1/skills/promote-requests` 与既有 `/v1/skills/{skill_id}`(UUID path param)冲突(非 UUID 段 422 而非 fall-through)。**archive 不新建端点**:复用既有 `PATCH /v1/skills/{id}` status=archived(能力已在,不重复)。
- 读:`GET /v1/skills`(扩展过滤,已有)、`GET /v1/skill-evolution/skills/{id}/eval-results`、`GET /v1/skill-evolution/skills/{id}/lineage`(skill + versions + forked_from 源)、`GET /v1/skill-evolution/promote-requests?status=&tenant_id=*`(review 队列)。
- 写:`POST /v1/skill-evolution/skills/{id}/promote-requests`(发起,admin 代发或 agent 工具走 SE-7)、`POST /v1/skill-evolution/promote-requests/{rid}/approve|reject`。
- kill-switch(SE-8-3):`GET /v1/skill-evolution/kill-switch`、`POST .../engage`、`POST .../release`(scope 感知:tenant scope 校验 = 本租户;global = 仅 system_admin)。

#### SE-8 实现顺序(拆 6 PR;设计先行 → 数据 → API → UI)
| 子 PR | 内容 | CI 边界 |
|---|---|---|
| **SE-8-0** | 本细化设计(已落本节)+ Mini-ADR + ITERATION-PLAN backlog | 设计 |
| **SE-8-1** | 迁移 0068(两表)+ DTO + 审计双 Literal + Store 方法(promote 审批 / kill-switch / list_skills 过滤);`decide_promotion` 加 `evolution_halted` 入参 | 单测;RLS/迁移仅 integration |
| **SE-8-2** | API:review 列表 / eval 证据 / lineage / approve-reject promote / archive,raw + audit + authz | control-plane(mypy 不覆盖,单测+手验) |
| **SE-8-3** | API:kill-switch engage/release/get + gate 接线(PromotionGate 读持久开关)+ app lifespan | 同上 |
| **SE-8-4** | UI:SkillsList 丰富(列+筛选+SDK 类型补 SE-1 字段)+ SkillDetail approve/reject/archive + i18n 双语 + Storybook + Playwright | admin-ui CI 门 |
| **SE-8-5** | UI:eval 证据 SVG 配对条 + lineage dagre+SVG + kill-switch 头部开关 + Storybook + Playwright | admin-ui CI 门 |

> **前端隐性缺口(SE-8-4 必含,否则漏做)**:`api/skills.ts` 的 `SkillRecord`/`SkillVersion` TS 类型**现完全无 SE-1 演化字段**(visibility/created_by_*/forked_from/evolution_origin/distilled_from_*/evolution_round),也无 `SkillEvalResult`/`SkillPromoteRequest`/`KillSwitch` —— 必须补镜像;router/Sidebar/CommandPalette **本 IA 不动**(无新页);i18n 必 zh-CN+en 双份;Storybook+Playwright 是 CI 门。

### SE-9 — self-evolution 基准 + SLO(Mini-ADR SE-A14)
- `tools/eval/datasets/self_evolution/`:构造"agent 反复踩同一坑 / 重复打法"的轨迹集 → 跑 worker → 断言:蒸馏出 skill 且 `delta>0` 且同类新任务成功率↑。证明闭环真有效(非 benchmark gaming:held-out 分离 + 确定性 mock + 真 integration 两套)。
- 性能 SLO:蒸馏延迟 / 重放延迟预算 + worker load-soak;进合并门(对标 Stream J.13 baseline 制品模式)。

---

## 6. 治理哲学:全自动 ≠ 无界自改

> [memory:no-design-choice-disguise](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_no_design_choice_disguise.md):不允许把弱护栏说成"够用"。

"尽量全自动"的安全模型 = **用强验证门替代人审,而不是取消把关**。四道永不松动的硬护栏:
1. **高危永远人审**:`is_high_risk_skill_version`(exec_python/exec_shell/http 或 scripts/* 路径)→ active 必须 admin(复用 U-24)。
2. **跨边界永远人审**:agent_private→tenant 需达标(可配是否自动);tenant→平台 / 跨租户**永不自动**。
3. **必须有证据**:任何自动 active 必须有 `skill_eval_result(verdict=pass)`(SE-A0)。
4. **可回滚 + 可熔断**:退化自动 archive;异常自动熔断降级全人审。

防伪进化(报告 § 7):归因丢弃环境/执行型失败(EmbodiSkill+hermes 清单);验证器与生成器分离(CoEvoSkills);held-out 与蒸馏来源分离(SPARK);定期掺真实/golden 信号防自噬。

---

## 7. 实现顺序与依赖

```
SE-0(本设计) ─► SE-1(数据模型) ─► SE-2(store) ─┬─► SE-3(Layer A 自著,可独立先上,= J.7b-1)
                                               └─► SE-4(重放验证,咽喉) ─► SE-5(蒸馏+归因) ─► SE-6(Layer B worker)
                                                                                              └─► SE-7(护栏) ─► SE-8(API/UI) ─► SE-9(基准/SLO)
```
- SE-3 可在 SE-4 之前交付(它本身就是 J.7b-1,对标三仓的"能自著"),但**默认产出停 DRAFT**,直到 SE-4/SE-7 就绪才允许自动 active。
- SE-4 是所有自动化的前置(没有验证门,任何自动 active 都不允许)。

---

## 8. Verification(端到端怎么证明)

- **单测**(`-m "not integration"`,CI 门):DTO / store(visibility 隔离、RLS)/ 4 工具 / replay 打分方向 / 蒸馏 / 归因(环境失败被判"不学")/ 策略(高危拦截、熔断、回滚触发)。
- **集成**(真 PG):迁移 0065 + RLS + worker `run_once` 全链 + 真 Haiku judge 重放。
- **端到端**:踩坑轨迹 → 蒸馏 → 重放 delta>0 → 自动 active → 同类新任务成功率↑(SE-9 基准)。
- **安全**:高危自动通道被拦 / 跨租户被拦 / 注入回归触发回滚 / 熔断生效 / agent_private 不泄漏到他 agent。

---

## 9. CI / 约束(复用 helix 门禁)

- 迁移 0065 revision id ≤ 32 字符(0058 已占用、链头 0064)([memory:alembic_revision_id_32_chars](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_alembic_revision_id_32_chars.md));只在 integration 验证。
- `audit.py` 双 Literal(protocol+control-plane);mypy 不覆盖 control-plane/src([memory:ci_lint_type_test_scopes](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/reference_ci_lint_type_test_scopes.md));pytest `-m "not integration"`。
- CodeQL:审计 / 日志脱敏 —— 别 log 请求派生值([memory:codeql_log_injection_request_taint](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_codeql_log_injection_request_taint.md))、别 log secret 命名名字、assert 无副作用、Protocol 体用 docstring、不留未引用 module-level 名。
- 协议改签名 sweep 含 tools/eval doubles([memory:protocol_sweep_includes_tools_eval](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/reference_protocol_sweep_includes_tools_eval.md))。
- skill 表 RLS:**ENABLE-only 不能加 FORCE**([memory:skill_curator_owner_rls_exemption](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/reference_skill_curator_owner_rls_exemption.md)) —— evolution worker 跨租户 sweep 依赖 owner 豁免,加 FORCE 会静默停扫。
- 每 PR:push 前 `pre-commit run --all-files`([memory:uv_lock_and_precommit_ruff](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_uv_lock_and_precommit_ruff.md) / [ruff_strict_lint_traps](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_ruff_strict_lint_traps.md))+ 同步 ITERATION-PLAN(checkbox+PR#,[memory:iteration_plan_sync_after_ship](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_iteration_plan_sync_after_ship.md))+ 零技术债收尾([memory:feedback_zero_tech_debt](../../.claude/projects/-Users-mac-src-github-jone-qian-helix-agent/memory/feedback_zero_tech_debt.md))。
- 不碰 `infra/.env.example`(用户本地工作树改动)。

---

## 10. agentic-harness-engineering 借鉴增强(SE-10 ~ SE-15)

深度对照外部研究仓 `agentic-harness-engineering`(Terminal-Bench 2.0 #3,「模型冻结、进化 7 正交 harness 组件」)后,提炼 6 个增量子项。三原则:**泛化复用现有验证门、不新建并行子系统、守代码进化红线**(工具实现/中间件/子agent 代码不进化,强制人审)。完整设计见各子项小节;对账见 `docs/research/2026-06-08-hermes-vs-stream-l-harness-reconciliation.md`。

| 子项 | 借鉴点 | 状态 |
|---|---|---|
| SE-10 | 进化对象从「仅 skill」扩到文本类组件(system_prompt 增量 / tool 描述 / 长期记忆),复用同一重放门 | ✅ 已落 |
| SE-11 | 变更「预测—自动证伪」纪律(predicted_impact → verdict),叠加不替代回滚 | ✅ 已落 |
| SE-12 | 分层 + 带源回链失败报告(Agent Debugger 式 overview/detail) | ✅ 引擎已落(投递面 SE-12b) |
| SE-13 | 进化前领域预研(冷启动 + KB/web → DRAFT 先验) | ✅ 已落(wiring 跟进) |
| SE-14 | Best-of-N 候选多样性(策略 hint 分流 + winner 选举) | ✅ substrate 已落(编排 SE-14b) |
| SE-15 | harness 规范 + linter + profile 对账 | ✅ 已落 |

### SE-10 — 文本类 harness 组件进化扩展(Mini-ADR SE-A15 / A16 / A17)

把 SE 进化对象从「仅 skill」扩到三类**无执行风险文本组件**(C1 system_prompt 增量 · C2 tool_description 补充 · C3 memory_entry),全部复用同一 `SkillVersion` 载体 + with-vs-without 重放门 + 治理门。代码类组件**不进化**(红线)。

- **SE-A15 数据模型**:`skill` 加 `component_type`(CHECK 四值)+ `target_tool_name`(⇔ `tool_description`),迁移 `0069_skill_component_type` 纯增量无新表/RLS;`Skill` DTO + `ComponentType` Literal + 互斥校验。泛化 `SkillVersion` 不新建表(复用单一重放门)。
- **SE-A16 装配渲染**:三类注册为 skill ref,replay 注入维度(`spec.skills`)不变,`GraphReplayTaskRunner` 零改;`_load_skills` 按 `result.skill.component_type` 分流渲染为 `<behavior-patch>`/`<tool-note>`/`<long-term-memory>` advisory 系统提示块(同 J-23 §15.6(c) 红线,**不改 ToolSpec**);文本组件不带 tools/不进 skill_view,但入 `resolved_versions` 供 SE-7 回滚;resolver 无 `skill` 回退 `skill`(向后兼容)。
- **SE-A17 治理无需改 `decide_promotion`**:读码证实 agent_private→tenant 的可见性提升本走独立 `skill_promote_request` 人审流,C1 跨租户永人审已被现有两段式治理满足;auto-active 仅作用该 agent 自身 run。
- in-session 3 builtin `note_behavior_patch`/`clarify_tool_usage`/`remember`(复用 AuthorSkill 全纪律,恒非高危)。**SE-10b 跟进**:Layer B 蒸馏产文本组件 + SE-9 eval component 场景。验证:`test_skill_component_evolution.py`(6 测)。

### SE-11 — 进化变更「预测—自动证伪」纪律(Mini-ADR SE-A18 / A19)

闭合「promote 时重放预测 → 上线后证伪」环,**叠加不替代**回滚。
- **SE-A18**:replay-derived 预测**就是** `skill_eval_result` 行(baseline_score→skill_score),无冗余预测表(generator 自陈源推迟 SE-11b);迁移 `0070_skill_pred_verdict` 单表;`SkillPredictionVerdict` DTO + `PredictionVerdict` Literal。纯逻辑 `decide_prediction_verdict`:`realized_fraction` 版本级带判定(effective/partially/ineffective/mixed/harmful/insufficient)。
- **SE-A19**:verdict 并入 `RollbackMonitor` sweep 同窗口同输入,与 `decide_rollback` 各产各的(rollback 决定 archive,verdict 只诊断 best-effort 不阻塞)——守不信 LLM 自评当下线门。验证:`test_skill_prediction_verdict.py`(6 纯逻辑 + 3 集成)。

### SE-12 — 分层带源回链失败报告(Mini-ADR SE-A20 / A21 / A22 / A23)

借鉴 Agent Debugger:两层根因聚类 + 证据回链,作生成器输入信号 + 审阅证据,**加层不替换归因、绝不进门控**(SE-A20/A23)。
- **SE-A21**:程序化预聚类零 token(按 attribution_kind/exit_phase/marker 分桶)+ 每桶一次 aux 命名(规则/环境桶跳 LLM,token 随桶数非 case 数)。
- **SE-A22**:PII type-level prompt + abstraction guard 降级 + snippet cap(240)。
- **实现**:`failure_report.py` `FailureReportBuilder`(`SkillAttributor` + 注入 `ReportSummarizer`,CI fake)+ pydantic DTO(control-plane 内部 JSON blob,非 protocol)。**SE-12b 跟进**:ObjectStore blob + API + admin-ui FailureReportPanel(i18n+Storybook+Playwright)+ 喂 `_TrajectoryEvidenceProvider`。验证:`test_failure_report.py`(6 测)。

### SE-13 — 进化前领域预研(Mini-ADR SE-A24 / A25 / A26 / A27 / A28)

借鉴 explore-agent:进化前研究领域(本租户 KB + 可选公网)产 DRAFT 先验喂生成器。
- **SE-A24** 冷启动 + TTL(纯逻辑 `needs_research`);**SE-A25** 产物=DRAFT agent_private 先验(origin=distilled,**绝不自动 active** 守 SE-A0);**SE-A26/27** 输入=本租户 KB(+可选 web,默认 off)只读本租户、不用平台 key;**SE-A28** 成本:冷启动+TTL+web 默认 off+abstraction guard。
- **实现**:`domain_research.py` `DomainResearcher`(KB/web/summary 三 Protocol 注入,CI fake)+ settings(默认 off)。**SE-13 wiring 跟进**:worker lifespan 注入真 KnowledgeRetriever/WebSearchTool/aux + 先验喂 distill(integration,同 SE-6d)。验证:`test_domain_research.py`(9 测)。

### SE-14 — Best-of-N 候选多样性(Mini-ADR SE-A31 / A32 / A33)

co-evolve 拓宽为 N 角度并行选 winner(拓宽 × co-evolve 加深,正交叠加)。
- **SE-A31** `DistillHint`(prompt/tools/anchored)经 `distill(hint_text=)` 注入,仅引导侧重不放宽两道 guard;**SE-A32** 纯逻辑 `pick_winner` 只选 pass、确定排序键 + name 兜底、无 orchestrator 依赖;**SE-A33** `BestOfNConfig.hints=()` 默认 OFF(opt-in)。
- **scope**:本 PR 交付 substrate(`skill_best_of_n.py` + distiller hint,10 测);**SE-14b 跟进 = processor fan-out 编排**(N 独立 DRAFT→各 replay→pick_winner→落选 archive→winner 进 evolve;需扩 ReplayOutcome + 重构已重测的 `__call__`)。

### SE-F(SE-15)— harness 规范 + linter(Mini-ADR SE-A34 / A35 / A36 / A37)

helix 是**声明式 harness**(`AgentSpec` manifest 装配成 `BuiltAgent`),非外部那种文件树式工作区。SE-15 把外部 HARNESS.md v1.0 的 7 组件**映射到 AgentSpec 字段**(规范 `docs/HARNESS-COMPLIANCE.md`),并补一个可执行校验器,覆盖 `model_validator` 表达不了的跨组件 / 命名 / 合规规则。

- **SE-A34 映射而非照搬**:7 组件 ↔ AgentSpec(system_prompt+dynamic_context / tools / 工具注册 / middleware / skills / subagents / memory.long_term);不复制外部目录结构(声明式 vs 文件树是不同形态)。
- **SE-A35 linter 只补 validator 覆盖不到的**:R1 schema 合规(error)/ R2 builtin∈KNOWN_BUILTINS(error)/ R3 启用的 HIGH_RISK_TOOLS ⊆ approval_required_tools(error,manifest 层映射 SE 高危永人审)/ R4 名 kebab(warn)/ R5 system_prompt 不嵌代码(warn,正交性)/ R6 vision 与视觉模型互斥(warn)/ R-prof profile 推荐块(warn)。误杀缓解:启发式归 warn,硬规则才 error。
- **SE-A36 纯静态接 CI lint job**:`tools/harness/check_harness_compliance.py`(stdlib+pydantic+pyyaml),扫 `manifests/**` 的 `kind: Agent`。R2 需 orchestrator 取 `KNOWN_BUILTINS`,不可导入时**打印 notice 跳过**(不静默放过;CI 必装全量 workspace 故必跑)。不接 mypy/pytest scope。
- **SE-A37 profile 复用思想不照搬工具**:`tools/harness/helix_profile.yaml`(对标外部 hermes/codex/openclaw profile 的「不同形态不同必填」思想);外部 `validate_harness.py`(文件树审计器)与 manifest 形态不符,**不进 CI**。

验证:`tools/harness/test_harness_compliance.py`(15 测,各规则正/反例 + canonical manifest 合规自检,CI 自检证非误杀)。

---

## 11. Mini-ADR 索引

| ID | 决策 | 章节 |
|---|---|---|
| SE-A0 | 验证门单一收口:自动 active 必须有 pass 证据 | § 2 |
| SE-A15 | SE-10 进化对象扩展=泛化 SkillVersion + `component_type`,不新建表(复用单一重放门) | §10 SE-10 |
| SE-A16 | 三类文本组件渲染为 advisory 系统提示块(behavior-patch/tool-note/long-term-memory),不改 ToolSpec;replay 维度不变 | §10 SE-10 |
| SE-A17 | C1 治理无需改 decide_promotion:跨租户人审已由现有 promote_request 流满足;auto-active 仅限 agent_private | §10 SE-10 |
| SE-A1 | 数据模型纯增量 + NULL-tenant RLS | § 4 |
| SE-A2 | `skill_eval_result` 作 grounding 可溯账 | § 4.3 |
| SE-A3 | SkillStore 演化 API + visibility 过滤 + §15.7 权限矩阵 | SE-2 |
| SE-A4 | 4 个自著工具(拆分语义)+ provenance + 默认 DRAFT | SE-3 |
| SE-A5 | grounding 判定:配对显著性(p<α)∧ delta≥θ ∧ n≥N ∧ 无新失败 | SE-4 |
| SE-A5b | grounding 信号强度三级分流(T1 硬verifier全自动 / T2 校准GenRM+锚点限定自动 / T3 人审)| SE-4 + SE-7 |
| SE-A6 | 验证确定性 / 成本:CI scripted + integration 真 judge;高危走沙箱;judge 用 swap-order 配对消位置偏 | SE-4 |
| SE-A7 | 蒸馏:对比式(成功+失败,SkillGen)+ 抽象 guard(防过度具体)+ 只取后验证据(SPARK)| SE-5a |
| SE-A8 | 失败归因:规则前置(锚 Aegis taxonomy)+ LLM 兜底两阶段;只粗粒度二分类(不步级);执行/环境错不喂回(防伪进化)| SE-5b |
| SE-A9 | co-evolve 有界轮 + 生成/验证器分离 + 每轮掺 1%–10% 可验证锚点防坍缩(CoEvoSkills + 1% 锚点)| SE-6 |
| SE-A10 | auto-promote 策略 + 边界 | SE-7 |
| SE-A11 | 回归回滚:`skill_run_usage` 专表 skill-centric 归因 + per-version 判定 + 单侧二项(非配对)+ 绝对地板 + 喂熔断 | § 4.4 / SE-7d |
| SE-A12 | 速率限制 + 熔断 | SE-7 |
| SE-A13 | admin review / lineage / 证据 / 紧急停 | SE-8 |
| SE-A13b | promote 审批用专表 `skill_promote_request`(正交 status;可查待审队列+决策审计+历史);租户 admin 管本租户 / system_admin 跨租户 | SE-8 |
| SE-A13c | 持久 kill-switch 专表 `skill_evolution_kill_switch`(scope global/tenant;补 in-process breaker 持久化缺口;喂 `decide_promotion` 作 halt 输入);与 archive(停单 skill)正交 | SE-8 |
| SE-A13d | 前端不新建页/不加导航:丰富 Skills 列表(live vs latest + 可见性/来源/verdict/待审,筛"仅待审"=队列)+ SkillDetail 展开全貌 + 列表头 kill-switch 开关 | SE-8 |
| SE-A14 | self-evolution 基准 + SLO 合并门 | SE-9 |
| SE-A34 | harness 规范 = AgentSpec 的 7 组件映射,不照搬外部文件树目录 | SE-15 |
| SE-A35 | linter 只补 model_validator 覆盖不到的跨组件/命名/合规规则(R1-R6+R-prof) | SE-15 |
| SE-A36 | 纯静态脚本接 CI lint job;R2 缺 orchestrator 时打印 notice 跳过不静默 | SE-15 |
| SE-A37 | profile 复用「不同形态不同必填」思想;外部 validate_harness.py 不进 CI | SE-15 |
| SE-A18 | 预测—证伪:replay 预测=eval 行(无冗余表),单 verdict 表;realized_fraction 版本级带判定;generator 自陈源推迟 SE-11b | §10 SE-11 |
| SE-A19 | verdict 并入 rollback sweep 同窗口同输入,叠加不替代(只诊断+反哺,下线仍由 decide_rollback) | §10 SE-11 |
| SE-A20 | 失败报告加层不替换归因;当输入信号/审阅证据,绝不进 decide_grounding | §10 SE-12 |
| SE-A21 | 程序化预聚类零 token + 每桶一次 aux 命名;规则桶跳 LLM(token 随桶数) | §10 SE-12 |
| SE-A22 | 失败报告 PII:type-level prompt + abstraction guard 降级 + snippet cap | §10 SE-12 |
| SE-A23 | 失败报告消费=喂生成器输入信号 + UI 下钻;DTO control-plane 内部 JSON blob 非 protocol | §10 SE-12 |
| SE-A24 | 领域预研触发=冷启动+TTL(非每轮);纯逻辑 needs_research | §10 SE-13 |
| SE-A25 | 预研产物=DRAFT agent_private 先验,绝不自动 active(守 SE-A0) | §10 SE-13 |
| SE-A26/27 | 预研输入=本租户 KB(+可选 web,默认 off);只读本租户不跨租户不用平台 key | §10 SE-13 |
| SE-A28 | 预研成本:冷启动+TTL+web 默认 off+结果上限+abstraction guard | §10 SE-13 |
| SE-A31 | Best-of-N hint(prompt/tools/anchored)仅引导侧重,不放宽 distiller 两道 guard | §10 SE-14 |
| SE-A32 | winner 纯逻辑 pick_winner:只选 pass,确定排序键+name 兜底,无 orchestrator 依赖 | §10 SE-14 |
| SE-A33 | BestOfNConfig.hints=() 默认 OFF(opt-in);fan-out 编排=SE-14b | §10 SE-14 |
| SE-A38 | 隐式正信号 IMPLICIT_SUCCESS:纯规则判定(无👎+无短时重发+success),不用 LLM | §12 SE-16 |
| SE-A39 | 👎 变燃料:作 failures 语料(带 comment)喂对比蒸馏,不单独成候选;仅有👎无正例仍跳过 | §12 SE-16 |
| SE-A40 | 蒸馏 transient 异常不烧候选:retry_count 列,≥3 或永久错误才标 evolved_at | §12 SE-16 |
| SE-A41 | per-tenant 灰度:tenant_config `skill_evolution_enabled`(默认 false)与平台总闸两层与 | §12 SE-16 |
| SE-A42 | 晋升→生效补链:AgentSpec `auto_attach_evolved_skills` opt-in,不偷改 manifest | §12 SE-16 |
| SE-A43 | aux LLM 计量进 token_usage(真 tenant_id + 源 agent),进化成本可 chargeback | §12 SE-16 |
| SE-A44 | implicit / judge-scored 来源永不 auto-promote(弱标注→强制人审累积信任);👍 来源可 auto | §12 SE-16 |
| SE-A45 | judge 抽样评分=候选提纯(默认 5%,便宜模型),与晋升闸 judge 体系分离 | §12 SE-16 |
| SE-A46 | 打标=校准集+语料非主粮:Playground 👍/👎 补落地(端点 G.6 就绪但无调用方)+ 业务系统 API 文档化 | §12 SE-16 |
| SE-A47 | 蒸馏入库查重:embedding 相似→转既有技能修订轮,不新建;merge 不做 | §12 SE-16 |

---

## 12. SE-16 — 进化飞轮可运转性 uplift(2026-07-02 全链评估驱动)

> 触发:用户问「进化自建 Skill 完整逻辑」→ 全链只读评估。结论:**验证-晋升-回滚闭环成熟,
> 但飞轮不可运转** —— 输入端信号饥饿、输出端生效断链、生产端无灰度。本节记录核实结论
> 与修复设计;实施拆 5 个 PR(见 § 12.9)。

### 12.1 核实结论(2026-07-02,file:line 证据在评估记录)

| 疑点 | 结论 |
|---|---|
| 晋升闸是否会被 judge-only 证据穿透 | **否,已达标**:`grounding._auto_eligible` 三层 tier —— T1 HARD_VERIFIER 自动;T2 CALIBRATED_JUDGE 需 anchors_passed AND judge_calibrated;T3 UNVERIFIED 永不自动。无需收紧 |
| 技能注入是否全量膨胀 prompt | 否:manifest 显式列表 + eager fragment / summary 两档,`lazy_load` 已有。**但反向问题成立**:ACTIVE 后不自动进任何 agent(见 12.5) |
| rollback 对照组 | 线上 with 流量 vs **晋升时点 replay baseline**(跨时段,漂移可污染);熔断兜底。短期接受,局限记录在案(§ 12.8) |
| aux LLM 计量 | **不走 token_usage**;judge 用 `_NULL_TENANT=UUID(int=0)` 占位。平台垫付糊涂账(见 12.7) |

### 12.2 断点总图

```
信号 ──✂ 饥饿(成功路只认👍;👎收集了不用;transient失败烧候选)
  → 候选 → 蒸馏 → 重放验证 → 晋升闸(✓成熟)
                                → ACTIVE ──✂ 断链(无 agent 装配 → 无 with 流量
                                              → last_used_at 不 bump → 30/90 天被 curator 收走)
生产 ──✂ 无灰度(平台级 bool,全租户齐开齐关 → 默认 False 至今)
```

### 12.3 信号金字塔(P0)— SE-A38 / A44 / A45 / A46

> 业界对标(2026-07-02 调研):生产环境显式打标覆盖率 **<1%**(乐观 1-3%),且强负偏;
> 没有严肃玩家把打标当主信号。主流 = 三层金字塔:隐式行为信号(100% 覆盖)→
> LLM-as-judge 抽样评分(1-5% trace,observability 生态标配)→ 显式打标(稀疏校准集)。
> Agent 技能自进化论文群(Voyager 谱系 / Trace2Skill / SkillsVote / SkillAudit)更彻底:
> 全走轨迹自动信号,无一依赖用户打标。原设计把 👍 当成功路主信号 = 权重错,本节重排。

**底座 — SE-A38 隐式规则信号**(覆盖 100% 成功流量,零 aux 成本):
`CurationWorker._classify` 对「无标记 + success」轨迹补纯规则判定:

- 该 thread 无任何 👎;
- 轨迹结束后**无短时重发**(同 thread 下一条 user 轮距上一 assistant 轮 < 5 分钟视为改口/重试
  —— 数据源 = `thread_message` 镜像,#892 现成;业界称 rephrase detection,教科书隐式信号);
- run 结局 success。

三条全过 → 新 `CurationSignal.IMPLICIT_SUCCESS` 候选。进 `EVOLVE_SIGNALS`。

**中层 — SE-A45 judge 抽样评分**(业界标配,恢复进本 wave):
对通过 SE-A38 规则的候选池,再抽样(`tenant_config` 可配,默认 5%)用**便宜 aux 模型**
批量评「会话质量分」(任务是否真完成、回答是否对题),高分才保留为候选。定位 =
**候选提纯**,不是晋升判定 —— 与晋升闸的 judge 校准体系(T2 tier)完全分离,不引入
晋升侧偏差。成本上限 = 抽样率 × 便宜模型,且计量走 SE-A43。

**顶层 — SE-A46 显式打标 = 校准集 + 语料,不是主粮**:
- 核实结论:`POST /v1/sessions/{thread_id}/feedback` 端点自 G.6 就绪,但**全仓无任何调用方**
  ——👍/👎 按钮从未落地(Stream H 漏项),打标率现状 = 0。
- 补两个入口:**Playground 每条 assistant 轮挂 👍/👎**(👎 弹 comment 框)——内部验收/
  试点人群打标率远高于终端用户,是灰度试点的信号主力;**业务系统代提交**写进对外
  集成文档(末端 UI 归业务系统是既定形态)。
- 角色:👍 候选权重最高(人背书);👎+comment 是 SE-A39 的失败语料;打标整体用于
  **校准 SE-A45 judge**(judge 评分与人工标注的一致率是 judge 可信度指标)。

**SE-A44 证据对齐**:implicit / judge-scored 来源候选蒸馏产物即便重放 T1 pass,
**auto-promote 强制降级人审**(`decide_promotion` 加 origin_signal 维度)。理由:输入是
弱标注,先经人审累积信任,数据够了再放开。👍 来源维持可 auto(人显式背书 + 重放独立验证)。

### 12.4 SE-A39 — 👎 变燃料(P0)

`negative_feedback` 不再被进化 worker 无视,但**不单独成蒸馏候选**(仅有失败样本会蒸出「别做 X」
的空洞技能):同 agent 存在 pending 的 positive/implicit 候选时,👎 轨迹(连同 `feedback.comment`,
字段已存在)注入 distiller 的 **failures 对比侧**。👎+success 结局 =「假成功」,对比信息量最高。
仅有 👎 无正例 → 保持跳过。

### 12.5 SE-A42 — 晋升→生效补链(P0,当前最短板)

核实:distilled 技能 ACTIVE 后**仍需 agent manifest 显式声明才被装配** —— 自动晋升的技能没有
任何消费者,`skill_run_usage` 无 with 流量,rollback monitor 无数据,`last_used_at` 不 bump,
30/90 天后被 curator STALE→ARCHIVED。**白蒸馏**。

两案对比后取 B:

- ~~A. promote 时自动改源 agent manifest~~ —— 偷改用户资产、触发 agent 版本变更,否。
- **B. `AgentSpec.auto_attach_evolved_skills: bool`(默认 false,表单露出)**:build 时若开,
  自动附加「本租户 + `evolution_origin='distilled'` + ACTIVE + 蒸馏源 agent_name=本 agent」的
  技能(lazy 档:summary 进 available-skills,不吃 eager prompt 预算)。显式 opt-in、
  per-agent 粒度、不动 manifest 资产。

附带成立:with 流量 → rollback 监控有数据;last_used_at 有 bump → curator 衰退语义恢复。

**业界对标注记**:技能消费主流是检索式(Voyager embedding top-5 / SkillFlow / selection-based
优于 full-injection 的实证)。lazy 档 = summary 列表 + 模型自选,即 selection-based 的一种,
在**单 agent 蒸馏产物量级(个位~十位)下等价且更简单**。演进触发条件写死:单 agent 附加
技能数 > 20 时,summary 列表本身成为上下文负担,转 embedding/BM25 检索预选(复用 HX-12
工具披露 BM25 先例)。不提前建设。

### 12.5b SE-A47 — 蒸馏入库查重(P2,业界对标补项)

对标发现的 gap:SkillNet / SkillOS / SkillBrew 等技能库系统标配 dedup / merge /
consolidation;helix 的 SkillCurator 只管**衰退**(STALE/ARCHIVED),无去重——同 agent
反复遇到相似问题会蒸出语义重复技能,信号扩容后产量上升,重复堆积会稀释 available-skills
列表并浪费人审带宽。

修:蒸馏产物**入库前查重** —— 与同 agent 现有 distilled 技能(DRAFT+ACTIVE)做 embedding
相似度(embedder 平台级已有);高于阈值 → 不新建技能,转为该技能的**修订轮输入**(走既有
revise 循环,version+1),人审面呈现「更新既有技能」而非新条目。低于阈值正常新建。
合并(merge 两技能为一)不做——修订轮语义已覆盖主场景,真 merge 等库规模需要时再议。

### 12.6 SE-A40 — transient 重试档(P0)

现状:蒸馏 processor 任何异常都标 `evolved_at`(幂等换 durability),aux LLM 限流/超时即永久
烧掉候选。修:异常分类 —— transient(限流/超时/连接)不标 `evolved_at`,候选加 `retry_count`
列(migration),worker 下轮重拾;`retry_count >= 3` 或永久性错误才标记放弃。

### 12.7 SE-A41 / SE-A43 — 灰度 + 计量(P1)

- **灰度**:`tenant_config` 键 `skill_evolution_enabled`(默认 false;读法照 `skill_curator`
  读 `skill_stale_days` 先例)。worker / rollback monitor 处理候选前查;平台 `enable_skill_evolution_worker`
  保留为总闸(两层与)。生产路径:开总闸 → 白名单租户逐个开 → live 试点。
  **admin-ui 维度**:开关必须有管理面——system_admin 租户管理页露出「技能自进化」开关
  (含 judge 抽样率输入);不接受「只能 SQL 改 tenant_config」的运维形态
  ([[feedback_iteration_items_need_frontend_audit]])。
- **计量**:distiller / judge / replay 的 aux 调用 token 写 `token_usage`(真 tenant_id、
  agent_name=蒸馏源 agent、evolution 专属 trace_id),替换 `_NULL_TENANT` 占位。进化成本
  进 chargeback 视图,租户账不糊涂。
  **区分维度(实施时定,倾向 A)**:进化用量混进源 agent 的对话成本会让运营分不清 ——
  A. `token_usage` 加 `usage_kind` 列(migration 小、查询显式);B. agent_name 命名空间
  (`skill-evolution:<agent>`,免 migration 但破 per-agent 聚合)。
  **admin-ui 维度**:用量视图按 kind 分列/过滤,进化成本单独可见。

### 12.8 显式不做(本 wave,均有业界对标依据)

- rollback 跨时段对照的统计弱点:业界正解 = shadow / champion-challenger(留部分流量跑
  without 对照),但流量成本对小租户不划算;熔断兜底 + 远期 shadow 是与业界一致的折中。
- 环境可验证信号(工具执行成功率 / 审批通过 / 产物被下载):agent 进化论文群的首选信号,
  但产物下载无审计埋点、工具成功率需归因建模 —— 进 backlog,埋点先行。
- 技能 merge(两技能合一):SE-A47 修订轮语义已覆盖主场景,库规模需要时再议。
- promote-requests 带外触达:事件发射复用 HX-9 webhook(`skill_promote.requested`),
  渠道接入与「审批 pending 通知」backlog 合并立项,不在本 wave。
- 候选队列可观测面(「哪些轨迹在排队蒸馏」的 admin-ui 列表):技能层 review 队列(SE-8)
  已覆盖决策点;候选层先靠 metrics + 日志,试点若证明运营需要下钻再立项。

### 12.9 实施拆分

| PR | 内容 | admin-ui 维度 | 依赖 |
|---|---|---|---|
| D | 本设计 | — | — |
| 1 | SE-A46 打标入口——试点信号主力,先行 | Playground 👍/👎 + comment 框(前端为主) | D |
| 2 | SE-A40 重试档 + SE-A41 灰度(解锁 live 试点) | system_admin 租户管理页「技能自进化」开关 | D |
| 3 | SE-A38/A39/A44 信号扩容(classify 升级 + EVOLVE_SIGNALS + failures 注入 + 弱信号禁 auto) | 无(候选可观测面显式不做,§12.8) | 2 |
| 4 | SE-A45 judge 抽样评分(候选提纯) | 抽样率输入并入 PR-2 开关面 | 3 |
| 5 | SE-A42 auto_attach_evolved_skills(spec 字段 + build 装配) | Agent 表单开关 + i18n | 2 |
| 6 | SE-A43 aux 计量(usage_kind 区分,倾向 A 案) | 用量视图 kind 分列/过滤 | 2 |
| 7 | SE-A47 入库查重(embedding 相似 → 转修订轮) | 人审队列区分「新技能 vs 更新既有技能」 | 3 |
| 8 | promote 事件发射(触达渠道另立项) | 无 | 5 |

验证:各 PR 单测 + 真 PG 集成;收官标准 = **live 试点全场景通过**(§ 12.10),
CI 绿≠live 能跑(★5 纪律)。

### 12.10 live 试点验证计划(收官 checklist)

> 教训在案:9.4 HA live 暴露 5 坑、4.4 真模型 E2E 逮 5 坑 —— live 计划必须先行成文,
> 不是收官时即兴。预期本轮同样逮 3-5 个 live 坑,修完才算 ★5。

**前置**:dev compose 栈 + 真 aux 模型 key(CI 无 model key,真 LLM 场景只能 live 跑);
PR 1-8 全合;灰度:平台总闸开 + `tenant_config.skill_evolution_enabled` 只开 test 租户;
test-agent 开 `auto_attach_evolved_skills`。

**阶段 A — 信号 → 候选**
1. Playground 对 test-agent:打 👍 一条、👎+comment 一条、一条成功对话不打标(等 5 分钟
   不重发)→ 断言 `curation_candidate` 出现 positive / negative / **implicit_success** 三类;
   另在同 thread 5 分钟内重发改口一次 → 断言该轨迹**不**产生 implicit 候选。
2. 造失败 run(max_steps)→ `failed_outcome` 候选。
3. **transient 重试**:临时改坏 aux key → 蒸馏失败 → 断言 `retry_count=1`、`evolved_at` 仍
   NULL;修回 key → 下轮自动重拾成功。连挫 3 次 → 标 evolved_at 放弃。
4. judge 抽样:implicit 候选池过 SE-A45 评分,低分被滤(日志可见),高分保留。

**阶段 B — 蒸馏 → 验证**
5. 👍 候选蒸馏 → DRAFT skill 落库,`skill_eval_result` 有 with/without delta;
   👎 comment 语料出现在 distiller 输入(trace 佐证)。
6. implicit 来源候选:重放即便 pass,**不 auto**,进人审队列(SE-A44)。
7. **查重**:再造语义相似的 👍 候选 → 断言不新建技能,既有技能 version+1(修订轮),
   人审面显示「更新既有技能」。

**阶段 C — 晋升 → 生效**
8. 人审批准 → ACTIVE;test-agent 新会话的 available-skills 出现该技能;`skill_run_usage`
   出现 with 行;`last_used_at` bump —— **生效断链修复的直接证据**。
9. auto 通道:eval_dataset 造 1 条带硬断言的持出 → T1 auto promote + `SKILL_EVOLUTION_AUTO_PROMOTED`
   审计行。
10. 限速:1 小时内制造 >5 次晋升 → 第 6 次降人审;kill-switch engage → auto 全停,
    disengage → 恢复。

**阶段 D — 监控 → 回滚 → 记账**
11. 人为造衰退(改坏已 ACTIVE 技能的 prompt_fragment 后跑真实流量)→ rollback monitor
    判定 → ARCHIVED + 熔断 record + `SKILL_EVOLUTION_ROLLED_BACK` 审计;熔断打开后
    新候选 auto 降人审(共享熔断验证)。
12. **计量对账**:全程 aux token 在 `token_usage` 可查且 tenant/agent 归属正确
    (无 `_NULL_TENANT` 残留),chargeback 视图可见进化成本。

**阶段 E — 运营面**
13. admin-ui:审批队列展示重放证据、kill-switch 交互、Playground 打标控件(axe 无 critical);
    未灰度租户确认全链静默(负路径:候选不生成、worker 不处理)。
14. webhook endpoint 收到 `skill_promote.requested` 事件。

产物:live 记录 + 坑清单落 docs(9.4 先例),全绿后 wave 关账。

## 附:与三篇论文 / 三仓的对应(可追溯性)

| 来源 | 思想 | 本 Stream 落点 |
|---|---|---|
| SPARK / PDI(2605.09192)| 后验、从环境证据蒸馏;held-out grounding | SE-5 蒸馏 + SE-4 重放(held-out 分离)+ `skill_eval_result.delta` |
| CoEvoSkills(2604.01687)| 生成器↔验证器共进化(无 ground truth)| SE-6 co-evolve 有界轮 + 生成/验证分离 |
| EmbodiSkill(2605.10332)| 区分技能内容错 vs 执行失误 | SE-5 归因 |
| hermes background_review | 后台隔离复查 + 不捕获环境失败清单 | SE-6 worker + SE-5 归因清单 |
| deer-flow skill_manage | 自著 CRUD + 写时安全扫描 + 历史归属 | SE-3 工具 + U-22 scan + provenance 列 |
| openclaw(反例)| 有 skill 基建但无生成闭环 | 警示:基建 ≠ 自进化 → 本 Stream 重心在闭环与验证 |
| SkillsBench(2602.12670)| 零验证自生成 skill 平均 −1.3pp、5 配置仅 1 改善 | SE-4 存在必要性的实证背书 → SE-A0「无 pass 证据停 DRAFT」 |
| CTA(2605.11946)| with/without 轨迹 diff + SIP;pass-rate 会饱和/抵消 | SE-4 轨迹级二级信号(|delta|<θ 时看中间阶段偏移) |
| Voyager(2305.16291)| 双通道验证(自检 + 客观环境反馈),真成功才入库 | SE-4 judge + assert 双信号 / SE-A0 入库前置验证 |
| 配对统计(McNemar/Wilcoxon/ICC,2512.06710)| 配对显著性 + 置信区间 + T≥5 重复 | SE-A5 升级为配对显著性检验(p<α ∧ delta≥θ ∧ n≥N) |
| 失败归因(ICML'25 Spotlight / DoVer / AgentNoiseBench)| 3 算法(All-at-Once/Binary/Step)+ 步级仅 14.2% 不可信 | SE-A8 只做粗粒度内容/执行二分类、不做自动步级定位 |
| 无预言机验证(GenRM/rubric/校准:UI-TARS-2 / 2601.15808)| 开放式任务用生成式 reward model + rubric + 人-judge 一致率校准 | SE-A5b 的 T2 限定自动路径 |
| 防坍缩(1% 锚点:2601.21268 / 2604.15149 / SRT)| 自奖励会坍缩;掺 1% 可验证真值即大幅压制 | SE-A5b T2 锚点门 + SE-A9 共进化每轮掺锚点 |
| **SkillGen(2605.10999)** | 把 skill 合成建模为 intervention;contrastive induction(成功+失败)→ gen-verify-refine → 按 held-out 净效应选 | **整体架构 1:1 印证(SE-4 净效应 + SE-5 蒸馏 + SE-6 环);SE-A7 升级对比式蒸馏** |
| 抽象/过拟合(Structured Agent Distillation 2505.13820 / MemSkill)| 单实例轨迹须抽象成处理整类;type-level 抽象防记忆碎片 | SE-A7 抽象 guard(prompt type-level + 拒绝含原值草案)|
| Aegis(2508.19504)| agent-environment 失败 6 模式 taxonomy(142 轨迹实证)| SE-A8 环境/执行错判定锚点(替代 ad-hoc 清单)|
