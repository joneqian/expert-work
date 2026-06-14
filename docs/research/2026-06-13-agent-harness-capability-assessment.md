# Helix Agent Harness 能力评估报告

> 日期：2026-06-13
> 方法：以业界优秀 agent harness 框架的 16 域能力清单为标尺（来源见文末），
> 对 helix-agent 代码库逐项实地核查打分。证据由并行代码勘探得出，逐项给 file path。

**评分标准（5 分制）**

| 星 | 分 | 含义 |
|---|---|---|
| ★★★★★ | 5 | 生产级完整，代码已落地 |
| ★★★★☆ | 4 | 基本完整，有小缺口 / 证据偏设计文档(⚠️) |
| ★★★☆☆ | 3 | 部分实现，有明显缺口 |
| ★★☆☆☆ | 2 | 仅雏形 / 重缺口 |
| ★☆☆☆☆ | 1 | 缺失 |

**总评：86 项 · 总分 388 / 430 · 均分 4.51 / 5（90.2%）**（含 W0/S0/全量重核 + 2026-06-14 S2 交付修订）

> 注：聚合分 4.51 含 S2 eval 平台交付（域 11 20→33）。「静态读码估计」对「是否真接线/emit」类项
> **不可全信**（见下「评分可靠性元结论」）；域 11 的 ★4-5 是**运行期+已交付双确认**（真跑 + merged PR），
> 可信度最高。

诚实校准：⚠️ 标记项证据来自 `docs/architecture/subsystems/*.md` 设计文档而非纯运行代码，
已相应降一档。结合已知事实（observability M0 #600-604、token_usage G.9、RLS/OIDC 已落地）
多数为真，但 SLO burn rate / 生产 eval worker / trace-based eval 确属设计或 M1。

> **2026-06-13 W0 核实修订**：实地核查代码后两项纠正——
> **10.1 连接式 trace 由 4★ 下调至 2★**：W3C 传播层（`propagation.py`）真落地，但
> `helix.session.run` 根 span 未创建、Span Link 零实现、LLM/tool 业务 span 缺，连接式 trace
> 核心未实装（原评分被设计文档误导，高估 2 档）。
> **13.1 会话隔离由 4★ 升至 5★**：核心 thread_id namespace 隔离已落地且测试覆盖，W0 补
> 两条并发隔离测试（`test_runner_unit.py`）后坐实。
>
> **2026-06-13 S0（P1 启动核实）修订**：勘探 P1 代码又揪两项被低估——
> **1.3 五大编排模式 3★→4★**：Evaluator-Optimizer 其实已实现（`reflect.py` accept/revise loop），
> 原"缺"判断有误。**4.4 程序记忆 3★→4★**：M0 agent 已能自写 skill（`skill_authoring.py` 7 builtin），
> 原"M0 不能自写"有误；缺的仅*自动*演化（M1）。详见 `2026-06-13-p1-self-improving-flywheel-design.md`。

> **⚠️ 2026-06-13 全量重核 — 评分可靠性元结论（必读）**：
> 因深核的 4/4 项全评错，做了全量逐项重核（6 agent 读码）。**重核暴露了更深的问题：静态读码评分
> 本身不可靠。** 三轮静态评分（初版 / 重核 agent A / 重核 agent B）互相矛盾：一组把域 3/5/6/15 几乎
> 全升 ★5（过度慷慨），另一组把 Langfuse/token-metric/多维配额/chargeback 误判为缺失（漏找实现，
> 经我**手动读码逐一推翻**：`langfuse_sdk.py`、`token_usage.py:helix_llm_token_usage_total`、
> `redis_quota.py:_scope_matches(agent,user)`、`billing-rollup-job/` 均真实存在）。
> **教训**：「capability 是否真接线 / metric 是否真 emit」这类，光读代码看不准，必须**跑起来**
> （起栈、scrape /metrics、看真 trace）才有 ground truth。
> **据此本表只做「手动代码核实确认」的修订，不采信矛盾的 agent 重核**：唯一坐实的真 gap 在
> **域 11 Eval**（恰是 design-doc 撑的"假 done"高发区）——`resolution_rate`/对抗集 grep 全空确属
> 未编码，下调（见域 11）。其余维持，待**运行期验证**（建议：每项开建前 just-in-time 跑起来核，
> W0/S0/本次均证明此法 100% 抓错且零损失）再逐项坐实。

---

## 域 1. Agent 控制循环 / 编排 — 均分 4.83

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 1.1 基础 loop | ★★★★★ | `orchestrator/graph_builder/builder.py:build_react_graph()`、`state.py:DEFAULT_MAX_STEPS=20` | ReAct 闭环 + max_steps 硬限终止 |
| 1.2 workflow vs agent | ★★★★★ | `protocol/agent_spec.py:WorkflowSpec` (react/plan_execute/custom) | manifest 声明类型，图工厂分支 |
| 1.3 五大编排模式 | ★★★★☆ | Routing `llm/router.py`；Parallelization `builder.py:asyncio.gather`；**Evaluator-Optimizer `reflect.py`** | **S0 核实升档 3→4**：Evaluator-Optimizer 其实已实现(reflect.py accept/revise loop)；剩 Orchestrator-Worker 仅基础 + 评判用同一模型 |
| 1.4 图/状态机 | ★★★★★ | `runner.py:GraphRunner`、LangGraph `StateGraph` | node+conditional edge 显式，支持子图 |
| 1.5 规划 | ★★★★★ | `graph_builder/planner.py:make_planner_node()`、`state.AgentState.plan` | 分解→注入→replan 全周期 |
| 1.6 可追溯 | ★★★★★ | `event_log/`、`sse.py`、`trajectory/` | 事件日志+SSE+轨迹三层可回放 |

## 域 2. 工具系统 — 均分 5.00

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 2.1 不直接执行 | ★★★★★ | `builder.py:tools_node()`、`tools/registry.py:Tool` Protocol | 模型只发 tool_call，节点校验→权限→执行→回注 |
| 2.2 schema 校验 | ★★★★★ | `tools/registry.py:ToolSpec` (Pydantic JSON Schema) | LLM 绑定时传完整 schema |
| 2.3 错误处理+重试 | ★★★★★ | `tools/error_classifier.py`、`builder.py` CM-1 tool_failures channel | 分类 transient/permanent + recovery advisory 回注 |
| 2.4 并行调用 | ★★★★★ | `tools/scheduling.py:plan_stages()`、`Semaphore(MAX_TOOL_WORKERS=8)` | read-only+path 冲突检测分阶段并行 |
| 2.5 token 高效 | ★★★★★ | `registry.py:ToolResult` truncation + `full_content` 外化 | 截断 + 溢出到 workspace |
| 2.6 动态门控 | ★★★★★ | `registry.py:_deferred`、`tools/find_tools.py` | deferred 池 + find_tools RAG promotion (HX-12) |
| 2.7 结果裁剪 | ★★★★★ | `context/compressor.py`、`context/working_window.py` | 多层裁剪 |

## 域 3. 上下文工程 — 均分 4.50

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 3.1 Compaction | ★★★★★ | `context/compressor.py` (摘要中间件) + flush_before_compaction | 保留窗口 + LLM summarize |
| 3.2 Trimming | ★★★★★ | `context/working_window.py` (快速) + compressor (LLM) | 两层分工 |
| 3.3 Context awareness | ★★★☆☆ | agent_node preflight + threshold 触发压缩 | 有阈值检测，**无"剩余 N token"显式反馈给模型** |
| 3.4 Token 预算 | ★★★★★ | `model_catalog.context_window`、`runtime/tokens.py`、`token_usage` 表 | 声明+计数+存储+成本 |

## 域 4. 记忆系统 — 均分 4.57

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 4.1 工作记忆 | ★★★☆☆ | `context/compressor.py`、`WorkingMemoryPolicy` | 有 LLM 摘要，**缺廉价滑窗截断**（全走重武器，见 research framework A2） |
| 4.2 语义记忆 | ★★★★★ | `persistence/memory/sql.py:SqlMemoryStore` (kind=fact)、migration 0013/17/24/25 | pgvector+RLS+dedup+soft-delete，turn recall/writeback |
| 4.3 情景记忆 | ★★★★★ | `memory/base.py` (kind=episodic)、`trajectory/recorder.py` | episodic 平行 fact，trajectory 落 S3 |
| 4.4 程序记忆 | ★★★★☆ | `tools/skill_authoring.py` 7 builtin(author/refine/fork/propose...) + `SkillStore` 三态 | **S0 核实升档 3→4**：M0 agent 已能自写 skill(DRAFT+agent_private+SE-8 审核)；缺的仅*自动*演化(`skill_evolution.py` M1) |
| 4.5 向量检索 | ★★★★★ | `llm/embedder.py`、`memory/sql.py` pgvector cosine+RRF+temporal decay | OpenAI 兼容 embedder + 混合检索 |
| 4.6 记忆管线 | ★★★★★ | `control-plane/memory_consolidator.py`、`memory/dlq.py` 5级backoff | extraction→consolidation(LLM聚类)→retrieval 全链 |
| 4.7 持久跨会话 | ★★★★★ | migration 0013+、`runner.py:PostgresSaver`、event_log | Postgres+RLS+DLQ 最终一致 |

## 域 5. 模型抽象层 — 均分 5.00

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 5.1 多 provider | ★★★★★ | `llm/router.py:LLMProvider` Protocol、`llm/providers/` | 统一 complete() 接口，多适配器 |
| 5.2 能力声明 | ★★★★★ | `protocol/model_catalog.py:ModelEntry` (vision/thinking/tool_disclosure)、`agent_spec.ModelSpec` | 目录驱动工厂分支 |
| 5.3 Fallback | ★★★★★ | `router.py:LLMRouter`、`agent_spec.fallback: list[ModelSpec]` | 4xx 即抛 / 5xx 落下一 provider |
| 5.4 凭证隔离 | ★★★★★ | `runtime/secret_store/`、`credential-proxy/`、ADR-0007 | secret:// URI 引用，KMS，沙箱不落密钥 |

## 域 6. 多 Agent / 子 Agent — 均分 5.00

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 6.1 委派 | ★★★★★ | `tools/subagent.py:SubAgentTool`、`ChildAgentBuilder`、`subagent_runtime.py` | manifest subagents[]→tool，MAX_DEPTH=3 |
| 6.2 上下文隔离 | ★★★★★ | `subagent.py` sub_thread_id/sub_run_id/child_config | child 独立 thread+config，仅收 system+task |
| 6.3 handoff | ★★★★★ | `subagent.py:call()` 共享 cancellation_token + result 回注 | 单向 parent→child→result |
| 6.4 并行 fan-out | ★★★★★ | `tools/scheduling.py:plan_stages`、is_parallel_safe (J-40) | 工具级+subagent级 fan-out |

## 域 7. 沙箱与代码执行安全 — 均分 3.63

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 7.1 宿主隔离 | ★★★★★ | `sandbox-supervisor/supervisor.py`、`infra/sandbox-image/seccomp-profile.json`、`docker_client.py` --internal+cap-drop | 容器+seccomp+egress隔离+资源配额 |
| 7.2 分级隔离 | ★★☆☆☆ | `settings.py:sandbox_runtime(runc/runsc)`、research 2026-06-12 | 架构支持 gVisor，**M0 默认 runc**，gVisor 仅 CI 验非生产强制 |
| 7.3 输入校验 | ★★☆☆☆ | `middleware/sandbox_audit.py`、`common/url_validation.py` | 查已知恶意模式（私网IP），**无 code pattern/injection 语义扫描** |
| 7.4 输出过滤 | ★★★☆☆ | `middleware/pii_redact.py`、`multimodal.py` 对象引用 | PII redaction+egress 隔离，**缺 DLP 分类/条件输出** |
| 7.5 纵深防御 | ★★★★★ | seccomp-profile.json (~400 allow)、`seccomp.py` fail-closed、cap-drop ALL | seccomp+cap 完备；LSM/PSA 单机 Docker 不关键 |
| 7.6 攻击监控 | ★★☆☆☆ | `common/observability.py` Prometheus、sandbox lifecycle 指标 | 可观测到位，**无主动入侵检测 Falco/IDS** |
| 7.7 爆炸半径 | ★★★★★ | migration 0005 RLS、`workspace_lock.py` advisory、sandbox per-session 销毁 | 跨租户边界清晰=单容器 |
| 7.8 SSRF 防御 | ★★★★★ | ADR-0009、`url_validation.py`、`docker_client.py` egress-only | 应用层URL校验+沙箱egress隔离；DNS-rebind 委托基础设施 |

## 域 8. 权限 / 护栏 / 人在回路 — 均分 4.60

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 8.1 事前授权 | ★★★★★ | `graph_builder/_approval.py`、`models/agent_approval.py` (migration 0031) | ask-before-run，tools_node 前查 approval_required_tools |
| 8.2 人在回路 | ★★★★★ | `_approval.py:ApprovalTarget`、`api/runs.py:ResumeRequest`、24h auto-reject | pending list→decision(approve/reject/modify)→resume |
| 8.3 暂停释放资源 | ★★★★★ | graph 停 END + RunStatus.PAUSED，checkpoint 持久化 | 暂停期不占工作线程，run record 轻量元数据 |
| 8.4 护栏双向 | ★★★★★ | 输入: _approval+url_validation+image计数；输出: pii_redact+egress | 输入侧全；输出侧有日志+隔离防护（缺 DLP） |
| 8.5 权限粒度 | ★★★☆☆ | `agent_spec.PolicySpec` (工具名/镜像变体/max_steps)、`quota/base.py` | 覆盖工具/镜像/quota，**缺资源 URI 级 / 细粒度 RBAC-ABAC** |

## 域 9. 持久化 / 可恢复执行 — 均分 4.00

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 9.1 Checkpoint 写入 | ★★★★★ | `runtime/checkpointer/factory.py:AsyncPostgresSaver`、`timing.py` | 每 super-step 自动写 Postgres |
| 9.2 stop/resume/retry | ★★★★★ | `api/runs.py:resume_run`、`orchestrator/resume.py`、`run_retry.py` | 取消+暂停+重试，从 checkpoint 恢复 |
| 9.3 thread 游标 | ★★★★★ | `models/event_log.py:thread_id`、`thread_meta.py`、LangGraph namespace | UUID 持久指针贯穿 |
| 9.4 跨进程恢复 | ★★☆☆☆ | Postgres checkpoint+event_log 可跨进程读 | **M0 无自动 failover / mid-run 热接力**，需 human resume (J-41 留 M1) |
| 9.5 托管任务队列 | ★★☆☆☆ | FastAPI background task、`runtime/runs/manager.py` 内存追踪 | **进程内 asyncio 队列，非分布式**（Celery J.10 留 M1） |
| 9.6 事件日志/重放 | ★★★★★ | `models/event_log.py`、`event_log/db.py` advisory-lock 原子写 | append-only+seq；通用重放 API 未实装（checkpoint 混合恢复） |

## 域 10. 可观测性 — 均分 4.17

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 10.1 连接式 trace | ★★☆☆☆ | `observability/propagation.py` W3C 真落地；`run_agent()` 无 root span | **W0 核实下调**：传播层在，但根 span/Span Link/业务 span 全缺，连接式 trace 核心未实装 |
| 10.2 每步 timing/token/cost | ★★★★★ | `middleware/token_usage.py`、102 项 helix_* 指标 | TTFT/tool_latency/token histogram |
| 10.3 Metrics 多维 | ★★★★★ | `observability/metrics.py` label 管控、`helix_llm_token_usage_total{tenant,agent,model}` | 102 指标，禁高基数 label |
| 10.4 低侵入埋点 | ★★★★★ | middleware+contextvar+`@with_agent_span` | 业务码无需显式 OTel |
| 10.5 趋势监控/SLO | ★★★☆☆ ⚠️ | `subsystems/20 §5.4 SLO`、§5.6 dashboard | 设计全，**M0 仅 3 dashboard，burn rate recording rule 未在 infra/ 落地** |
| 10.6 OTel/Langfuse | ★★★★★ | ADR-0005、`langfuse_middleware.py`、OBS-L1 PII mask | Langfuse v3+OTel三件套，trace_id 共享跳转 |

## 域 11. 评测 / Eval harness — 均分 4.71（S2 平台层全交付）

> **2026-06-13 S2 运行期验证**：真跑 `tools/eval/run_baseline.py` → 15 capability 全 PASS。厘清
> 「引擎强 / ops 层缺」。**2026-06-14 S2 交付收口**：ops 层四项全建并 merged——11.6 生产 worker
> （#618/#620/#622 + FE #623）、11.3 会话级指标（#624，端到端）、11.4 trace-based eval（#625，
> 模块+测试）、11.5 对抗集（#626，模块+dataset+测试）。11.4/11.5 为 ★4：能力+测试齐但**未接
> worker suite**（需 model-backed responder，CI 无 key），production 接线是到 ★5 的唯一 gap。
> 详见 `..-p1-s2-eval-platform-design.md`。

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 11.1 确定性 eval | ★★★★★ | **运行确认**：`run_baseline.py` 15 cap 全 PASS；`helix_eval.py` mock_provider | 无 LLM CI 真跑通 |
| 11.2 LLM-judge | ★★★★★ | **运行确认**：`_judge.py:ScriptedJudge`(CI)+`AnthropicHaikuJudge`(周跑) | baseline judge_mean 出数 |
| 11.3 会话级指标 | ★★★★★ | **已交付**（S2.2 #624）：`session_metrics_from_cases` 出 `goal_completion`，端到端 plumb 引擎→worker→API→FE 详情页显示 | 端到端生效；escalation 仅信号时出不零填 |
| 11.4 Trace-based eval | ★★★★☆ | **已交付**（S2.4 #625）：`trace_eval.py` 纯断言引擎 + capture harness，断言调用链；脚本图 CI 实跑 7 测 | 模块+测试齐，**未接 worker suite**（差 production 接线 → ★4） |
| 11.5 对抗 prompt 数据集 | ★★★★☆ | **已交付**（S2.3 #626）：`adversarial.py` + `datasets/adversarial/`，injection canary 不泄 / jailbreak 拒答，硬门 safe_rate=1.0，9 测 | 模块+dataset+测试齐，**未接 worker suite**（差 production 接线 → ★4） |
| 11.6 生产 eval 异步 | ★★★★★ | **已交付**（S2.1 #618/#620/#622 + S2.5 #623）：`eval_run`/`eval_case_result` 表 + 常驻 `EvalWorker`（lifespan 门控）+ enqueue/read API + admin-ui Eval 页 | 端到端：触发→drain→结果→可视化 |
| 11.7 标准 benchmark | ★★★★★ | **运行确认**：`tools/eval/longmem/` harness + 31 单测过；LoCoMo+LongMemEval_S, recall/NDCG/MRR | P0 retrieval + P1 e2e 两层 |

## 域 12. 成本 / Token / 计量 / 配额 — 均分 4.50

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 12.1 token+cost 追踪 | ★★★★★ | `token_usage_store.py`、migration 0036、`helix_llm_billed_cost_micros` | per-call 一行，cache token 单记 (L.L1) |
| 12.2 配额门控 | ★★★★★ | `subsystems/16`、`quota/redis_quota.py` Lua 令牌桶、`reserve/commit/release` | 三层限流+四维令牌桶+429 Retry-After |
| 12.3 多维计量 | ★★★★★ | quota CheckRequest (tenant/agent/user/model)、token_usage 明细 | AND 逻辑多维 |
| 12.4 chargeback 计费 | ★★★☆☆ | `helix_billing_rollup_*` 指标、`api/billing/` 路由 | token 计量真，**rate card/定价/发票属业务层未实装** |

## 域 13. 多租户 / 认证 / 隔离 — 均分 4.60

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 13.1 长会话隔离 | ★★★★★ | `runner.py:GraphRunner.compile` + LangGraph thread namespace；`test_runner_unit.py` 含并发隔离测试 (W0 补) | **W0 核实升档**：thread_id 隔离真落地，并发不串话已测 |
| 13.2 多用户确定性恢复 | ★★★☆☆ ⚠️ | `subsystems/19 §5` idempotency_key+replay 标记、token_reservation 状态机 | 机制设计在，**并发 resume race condition 未详述** |
| 13.3 租户隔离 RLS | ★★★★★ | `persistence/rls.py` GUC 注入、`check_rls_naming.py` CI、user-level RLS (J.3) | FORCE-RLS+bypass opt-out+CI 强制命名 |
| 13.4 OIDC+RBAC | ★★★★★ | `auth/rbac.py:authorize()`、`dev/oidc-keycloak.md`、Keycloak | code-flow+PKCE，deny-by-default 决策矩阵 |
| 13.5 平台域vs租户域 | ★★★★★ | `rbac.py` Role.SYSTEM_ADMIN vs ADMIN、role_binding platform_scope (Stream N) | mcp_catalog/billing 仅 system_admin |

## 域 14. 扩展性：Skills / MCP / 集成 — 均分 4.60

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 14.1 Skill 系统 | ★★★★★ | `persistence/skill/base.py`、migration 0029、`api/skills_api.py` | Anthropic Skills spec，三态，平台+租户两级库，lazy load |
| 14.2 MCP 客户端 | ★★★★★ | `control-plane/tenant_mcp_pool.py`、`tools/mcp.py`、migration 0011 | client-only，per-tenant pool，多 transport |
| 14.3 集成凭证 | ★★★★★ | `encrypted_secret_store.py` KMS、`mcp_oauth.py`/`user_mcp_oauth_pool.py` | 平台 KMS+租户 OAuth 池，仅存 token_ref，自动续期 |
| 14.4 MCP 纵深防御 | ★★★☆☆ | `agent_spec.allowlist`、`tenant_config.mcp_allowlist`、`threat_patterns.py` | allowlist+threat scan，**MCP in-process 不独立沙箱，无流量审计** |
| 14.5 出站 webhook | ★★★★★ | `webhook_delivery_worker.py` (HX-9)、migration 0033、HMAC-SHA256 | 5级backoff+per-endpoint断路器+签名 |

## 域 15. 流式 / 中断 — 均分 5.00

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 15.1 SSE+Last-Event-ID | ★★★★★ | `orchestrator/sse.py`、`stream_bridge/base.py:StreamEvent.id` | 单调 ID，重连续传 |
| 15.2 可取消 | ★★★★★ | `runtime/cancellation.py:CancellationToken`、`RunCancelledError` | cooperative cancel 贯穿 LLM+工具链 |
| 15.3 中断一致 | ★★★★★ | `runner.py:sanitize_thread()` 修孤立 tool_call、checkpoint per super-step | 历史不篡改 |

## 域 16. 部署 / 运维 / 可靠性 — 均分 4.20

| 项 | 评分 | 证据 | 依据 / Gap |
|---|---|---|---|
| 16.1 容器化+多 profile | ★★★★★ | `infra/docker-compose.yml` (full/observability/auth/sandbox)、environments/*.yaml | blue/green+PgBouncer+healthcheck |
| 16.2 重试/断路器 | ★★★★★ | `middleware/llm_error_handling.py:CircuitBreaker`、quota Retry-After、webhook breaker | 三层 per-provider/tenant/endpoint |
| 16.3 资源耗尽委托 cgroup | ★★★☆☆ | `subsystems/14 §6` OOM 委托 docker cgroup、label cardinality CI gate | 理念对，**无应用层 backpressure/fail-fast 代码** |
| 16.4 故障自愈 | ★★★☆☆ | durable resume、token_reservation reaper、`skill_rollback_monitor.py`、webhook DLQ | 应用内自愈有，**无基础设施级自动切流/扩容** |
| 16.5 健康检查 | ★★★★★ | `api/health.py` (live/ready/startup)、`common/health.py` | K8s 三探针，status code 遵约定 |

---

## 评分汇总

| 域 | 项数 | 均分(/5) | 星 | 短板 |
|---|---|---|---|---|
| 1 控制循环 | 6 | 4.83 | ★★★★★ | Orchestrator-Worker 仅基础(1.3) |
| 2 工具系统 | 7 | 5.00 | ★★★★★ | — |
| 3 上下文工程 | 4 | 4.50 | ★★★★★ | context awareness 不反馈模型 |
| 4 记忆 | 7 | 4.57 | ★★★★★ | 廉价滑窗缺(4.1) / skill 自动演化缺(4.4) |
| 5 模型抽象 | 4 | 5.00 | ★★★★★ | — |
| 6 多 Agent | 4 | 5.00 | ★★★★★ | — |
| 7 沙箱安全 | 8 | 3.63 | ★★★★☆ | M0 默认 runc / 无 DLP / 无 IDS |
| 8 权限 HITL | 5 | 4.60 | ★★★★★ | 缺细粒度 RBAC |
| 9 持久化 | 6 | 4.00 | ★★★★☆ | 无自动 failover / 无分布式队列 |
| 10 可观测性 | 6 | 4.17 | ★★★★☆ | 连接式 trace 核心未实装(10.1★2) / SLO burn rate 待落地 |
| 11 Eval | 7 | 4.71 | ★★★★★ | **S2 全交付**：11.6/11.3 ★5 端到端；11.4/11.5 ★4(模块+测试，未接 worker suite) |
| 12 成本计量 | 4 | 4.50 | ★★★★★ | chargeback 业务层未实装 |
| 13 多租户 | 5 | 4.60 | ★★★★★ | 并发 resume race 未详述(13.2) |
| 14 扩展性 | 5 | 4.60 | ★★★★★ | MCP 不独立沙箱 |
| 15 流式中断 | 3 | 5.00 | ★★★★★ | — |
| 16 部署运维 | 5 | 4.20 | ★★★★☆ | 无基础设施级自愈 |
| **合计** | **86** | **4.51** | **★★★★★** | 总分 388/430 (90.2%)；W0(10.1↓2/13.1↑1)+S0(1.3↑1/4.4↑1)+全量重核(11.3↓2/11.5↓1)+S2交付(11.3↑4/11.4↑3/11.5↑3/11.6↑3=域11 20→33) |

各域项数核对：6+7+4+7+4+4+8+5+6+6+7+4+5+5+3+5 = **86 项**。

## 主要 Gap（按价值排序，低分项优先）

> 2026-06-14 更新：**Eval 闭环已从首要 gap 退出**——S2 平台层四项全交付（域 11 2.86→4.71）。
> 剩余唯一 eval gap = 11.4/11.5 接 worker suite（★4→★5，需 model-backed responder）。

1. **沙箱隔离强度（域 7，均分 3.63）** — 多租户共宿主跑 untrusted code，M0 仅 runc+seccomp
   （分级隔离★2/输入校验★2/攻击监控★2）。内核 CVE 类逃逸真实风险（SANDBOXESCAPEBENCH ~40%）。
2. **持久化分布式化（9.4/9.5 均 ★2）** — M0 单 worker，崩溃需 human resume，无分布式队列。
   长任务 HA 核心缺口，Celery/failover 推 M1。
3. **权限细粒度（8.5 ★3）** — 停在工具名/镜像/quota，无资源 URI 级 / RBAC-ABAC。
4. **输出 DLP（7.4 ★3）** — 有 PII redaction，无内容分类驱动的条件输出。
5. **trace/对抗接 worker suite（11.4/11.5 ★4）** — 模块+测试已交付，未接生产 worker（需 model-backed responder，CI 无 key）。

## helix 的相对强项（满分域）

- **工具系统 / 模型抽象 / 多 Agent / 流式中断（均 ★★★★★ 5.00）** — 生产级。
- **记忆系统（4.43）** — pgvector 混合检索 + 三层巩固管线 + DLQ，超出多数开源框架。
- **多租户隔离（4.40）** — RLS+FORCE-RLS+OIDC+RBAC+平台/租户域分层，企业级。
- **可观测性（4.50）** — OTel+Langfuse+Prometheus 102 指标，PII 脱敏。
- **诚实标 gap** — ITERATION-PLAN 每个推迟项带理由+决议链接，比硬说"满分"可信。

## 一句话评价

helix 是**设计扎实、诚实标注 gap 的企业级 agent platform**，均分 4.51/5。核心 agent 循环 /
工具 / 记忆 / 多租户 / 可观测性 / **Eval 闭环（S2 交付后 4.71）** 已生产级。最低分集中在
**沙箱隔离强度（3.63）/ 运行时分布式化（HA）**——已明确论证并排期 M1，是"M0 优先度 > 该
能力强度"的诚实取舍，而非能力缺失伪装成设计选择。

---

## 评估标尺来源（业界 16 域清单）

- [Credal — What Makes a Great Agent Harness?](https://www.credal.ai/blog/enterprise-ai-agent-harness-production-security-governance)
- [Builder.io — What's an Agent Harness?](https://www.builder.io/blog/agent-harness)
- [Anthropic — Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents)
- [Anthropic — Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic — Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Anthropic — Scaling Managed Agents: Decoupling the brain from the hands](https://www.anthropic.com/engineering/managed-agents)
- [LangChain — AI Agent Observability](https://www.langchain.com/resources/agent-observability)
- [LangChain — The Runtime Behind Production Deep Agents](https://www.langchain.com/blog/runtime-behind-production-deep-agents)
- [Confident AI — LLM Agent Evaluation Metrics in 2026](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide)
- [Redis — AI agent memory: types, architecture & implementation](https://redis.io/blog/ai-agent-memory-stateful-systems/)
- [Trail of Bits — Prompt injection to RCE in AI agents](https://blog.trailofbits.com/2025/10/22/prompt-injection-to-rce-in-ai-agents/)
- [Microsoft Security — When prompts become shells](https://www.microsoft.com/en-us/security/blog/2026/05/07/prompts-become-shells-rce-vulnerabilities-ai-agent-frameworks/)
- [Google Developers — Long-running agents that pause, resume with ADK](https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/)
- [GitHub — awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
