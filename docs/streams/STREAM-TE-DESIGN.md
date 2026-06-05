# Stream TE — Tool Execution Engine Hardening(+ Stream OFFICE 办公能力包)

> 把"改进版 Tool 执行引擎"设计落地到 helix:在保留 helix 已成熟的并行调度的前提下,
> 补齐工具元数据 / 工具级审计与可观测 / side_effect 门控 / bash / tool RAG / 文件原语 + 跨副本锁 + edit 鲁棒化与 CAS,
> 并另开 Stream OFFICE 补足企业办公 70% 场景的工具面。
> 设计先行见 [[feedback_design_first_iteration]];零债收尾见 [[feedback_zero_tech_debt]];能力不可弱见 [[feedback_complete_not_minimal]]。

## 0. 来源与定位

源自一篇 Go「Harness(驾驭工程)」专栏(Tool Registry / 极简原语 / Parallel Tool Calling / 文件并发安全)。经 5 图事实分析 + 三个真实实现(`openclaw` TS / `deer-flow` Py / `hermes` Py)源码对照 + helix 三路现状探查,形成本设计。**结论:文章正确内核保留,其"本地可信 / 极简 4 原语唯一解 / 押注模型纪律"三个前提在多租户 server-side 下解开。** helix 定位 = 受治理的多租户 agent 平台(非本地全能 CLI),企业办公约占 70%(读多写少)。

## 0.1 背景 / 缺口(已 file:line 核实,2026-06-05)

- **Registry** ✅ 成熟:`tools/registry.py` `ToolSpec`/`ToolRegistry`;`tools/assembly.py:117` `build_tool_registry`;`graph_builder/builder.py` dispatch;主循环解耦。缺:子集暴露(全量 bind)。
- **工具面**:builtins=`KNOWN_BUILTINS`(web_search/exec_python/save_artifact/list_artifacts/ask_for_approval/ask_image,`tools/assembly.py:54`);MCP client 完整(`tools/mcp.py`,Stream V/W);skill lazy + `tools/skill_view.py`(部分 tool RAG,仅 skill 层)。缺:**无 bash/文件原语、工具层无 tool RAG、办公库/连接器薄**。
- **并行调度** ✅ **已成熟**:`tools/scheduling.py`(Stream L.L6)`plan_stages` + `is_read_only`/`path_args` 冲突检测 + 路径归一化(`PurePosixPath.as_posix`)+ `builder.py:395` `asyncio.gather` + Semaphore(8);子 agent 并行(J.4 `is_parallel_safe`)。缺:`irreversible` 串行档。**——这是文章最难的部分,helix 已有,不重做。**
- **路径锁** ❌ 无 per-path 锁(只全局 Semaphore);归一化部分(无 realpath);`tools/artifact.py:54` `_validate_path` 防 `..` 拒绝绝对路径。
- **CAS** ❌ 无 edit 工具、无 expected_hash;stale 只在 skill 漂移层(`skill_view.py`)。
- **沙箱** ✅ 强:`sandbox-supervisor`(gVisor+tmpfs+per-user workspace `helix-ws-{tenant}-{user}` `persistence/workspace/base.py`+超时 30/300s+quota)。**三家参考实现都没有这个级别的多租户隔离。**
- **审计/可观测**:`protocol/audit.py` `AuditEntry` + `TOOL_CALL`/`TOOL_BLOCKED` action **已定义**;trajectory recorder + Langfuse trace 已有。缺:**工具级审计 emit 未接线**(只 run-end `sse.py:_emit_run_end_audit`)、**per-tool 指标缺**、`side_effect` 级别未声明、审批靠硬编码 gated_tools(`tools/eval/hitl.py`)。

## 0.2 已锁决策(用户拍板,2026-06-05)

- **范围** = Tier1(元数据+审计+门控)+ Tier2(tool RAG)+ Tier3(文件原语+锁+CAS),全做。
- **bash IN**(见 TE-ADR-1)。**edit 必须鲁棒**(见 TE-ADR-6)。**跨副本锁做强做完整**(见 TE-ADR-3)。**性能进验收门 + 可观测补全**(TE-ADR-5)。
- **另开 Stream OFFICE** 补办公工具面(见 §3)。
- **优先级按"办公读多写少"杠杆重排**:可观测 / 工具面 / bash 前置(P0),锁 / CAS 押后(P2)但保留。

## 0.3 关键架构事实(TE-0 核实,决定 TE-ADR-3)

**workspace 不是单 sandbox 独占租约**(已核实):
- `supervisor.py:167` 每次 acquire 新建 `SandboxRecord`;`:196` 热会话表 `_sessions[(tenant,user)]` 仅保证"该用户单一 warm session",**不是 workspace 独占**(热会话过期/销毁/supervisor 重启后新 sandbox 仍挂同一卷)。
- `domain.py`/`store.py`:**无 workspace 状态机、无 `UNIQUE(workspace_id,state)`、无 DB 行锁/乐观锁**。
- `runtime_provider.py:122` 卷挂载无"已挂载检查",Docker 允许同名卷多容器并挂。
- supervisor 单进程 in-memory 状态(`_sessions`/`_exec_locks`),**多副本无协调**;`infra/README.md:103` 明令 `pg_advisory_lock`→用 `pg_advisory_xact_lock`(`DbEventStore` 已有先例)。

→ **in-process `asyncio.Lock` 不足以串行同一 workspace 文件 I/O。必须跨进程分布式锁 = PG `pg_advisory_xact_lock`。** 这把跨副本锁从"可选"定为"必须"。

## 1. Mini-ADRs

### TE-ADR-1 bash IN(改自初稿 OUT)
- **决策**:新增 `bash` 工具,跑在**现有 gVisor 沙箱内、限定 workspace**(经 sandbox-supervisor,见 TE-ADR-2)。
- **理由**:`exec_python` 已能 `subprocess.run` 任意命令——真正边界是 gVisor 沙箱,不是"有无 bash 工具";拒 bash 是洁癖。bash 是兜底(git/grep/pandoc/zip/格式转换),对办公场景高杠杆。
- **约束**:`side_effect` 默认判 `irreversible`(一条命令可能 `ls` 也可能 `rm -rf`)→ 串行 + 门控,除非模型声明读写集走"快档";bash 视为**沙箱内全局写锁**(覆盖其逃逸 path 锁的洞,见 TE-ADR-3)。超时复用 supervisor 30/300s。

### TE-ADR-2 文件原语执行 locus = exec-warm 通道(2026-06-05 复议改定)
- **决策**:文件原语(read/write/edit)经 **`run_in_sandbox` exec 通道**执行(像 `bash`/TE-5 那样把操作 `_build_wrapper` 成 python 跑在 J.15 warm 沙箱内),**复用 `persistent_workspace=True` 的 per-user 热沙箱**;orchestrator 进程仍**不直接碰 FS**。
- **复议背景(TE-7 探查推翻初稿)**:初稿倾向"supervisor 加专用 file API"。探查发现 supervisor 现有 `read_workspace_file` 的底层是**每次 `docker run` 起临时容器**(`docker_client.py:129-176`)——若 write/edit 照此模式,**每次文件操作冷启动一个容器(秒级)**,直撞用户反馈第 4 点"性能要有保障"。exec-warm 复用热沙箱(15min TTL,`run_in_sandbox`/`bash` 已走通),无冷启动(毫秒级)。用 AskUserQuestion 复议拍板走 exec-warm(见 [[feedback_design_doc_assumptions_age]])。
- **理由**:① 性能——复用 warm session,避开临时容器冷启动;② CAS 原子性——read→verify hash→atomic rename 在**单个 exec 内天然原子**,无需跨 HTTP 协调;③ surgical——纯 orchestrator 工具层,不动 sandbox-supervisor。
- **代价/兜底**:exec 跑 python 读写,二进制/超大文件(>10MiB)交给已有 `read_workspace_file` 专用 API 兜底;文件原语主打文本工作区操作。

### TE-ADR-3 跨副本锁 = PG advisory xact lock + per-workspace 排他写锁(必须,做强做完整)
- **决策**(据 §0.3 + 2026-06-05 粒度复议):**写**操作(文件原语 + bash)在 orchestrator 侧开 DB 事务 → `pg_advisory_xact_lock(hashtextextended("{tenant_id}:{user_id}", 0))`(**per-workspace 单锁**)→ 锁内调 `run_in_sandbox` 跑写 exec → 提交事务自动释放(事务级,合规 `infra/README.md:103`,镜像 `event_log/db.py:51-63` `_acquire_thread_lock`)。
- **为何 per-workspace 而非 per-path**:`bash`(`side_effect=irreversible`)无 path 参数、无法静态解析读写集;PG advisory 按**精确 hash 互斥且无共享读锁**,故 per-path 锁无法干净覆盖 bash 与文件写的互斥(全局 key 与 path key 的 hash 不同→不互斥)。per-workspace 单锁让 bash 与所有文件写**自然互斥**,绝对正确且简单。代价:同 user 跨副本/跨 run 写不同文件也串行——但同 turn 内 `scheduling.py` 已串行写、per-user workspace(单 agent 实例为主)并发写本就极低,损失几乎不存在,契合"读多写少"。(2026-06-05 AskUserQuestion 复议拍板,反转初稿 per-canonical-path)
- **读无锁 + 原子写消除"读串行"限制**:写 exec 用**临时文件 + `os.replace` 原子 rename**;原子 rename 保证读永远看到完整的旧/新快照,故**读/list 完全不取锁、永远并发**。advisory 仅排他无共享读锁的限制因此不影响读。
- **锁横跨 exec 的诚实代价**:一个 DB 连接被占用在整个写 exec 期间(acquire+exec,通常几百 ms);受 `idle_in_transaction_session_timeout=60s`/`lock_timeout=5s` 约束,写 exec timeout ≤30s 安全;写不高频,连接池压力可接受。RED 集成测验证并发写不交错。
- **path 归一化(服务越界防护/CAS,非锁 key)**:锁 key 只含 `tenant:user`;文件原语仍需 `PurePosixPath` 归一化路径(拒绝绝对路径 / `..`,与 `artifact.py:_validate_path`、`scheduling.py` 一致)+ 沙箱内 `os.path.realpath` confinement 防 symlink 越界(校验落在 `/workspace` 内)。这些用于越界防护与 CAS hash,不参与锁。

### TE-ADR-4 tool RAG(deferred registry + find_tools)对标 deer-flow
- **决策**:`ToolRegistry` 支持 deferred entries + `specs(selector)` 子集;新增 `find_tools` 元工具,查询语法对标 deer-flow `tool_search`(`select:name1,name2` / `+keyword rest` / regex)+ `promote()` 激活;bind 前 middleware 过滤未激活工具 schema(复用 `skill_view` 元工具模式 + `deferred_tool_filter` 思路)。
- **触发阈值**:工具总数(尤其 MCP catalog 增长)超阈值(设计期定,默认如 >25)时 MCP/低频工具默认 deferred;核心原语 + `find_tools` 常驻。

### TE-ADR-5 可观测 + 性能 SLO(从范围外提为范围内)
- **可观测**:per-tool Prometheus(`helix_tool_call_total{tool,outcome}` / `helix_tool_latency_seconds{tool}` / `helix_tool_error_total{tool}`,label 控基数不含 tenant 高基维)+ 每工具 Langfuse span + trajectory 富化(exit_code / 读写路径集 / 耗时)。
- **性能 SLO(验收门)**:文件原语经 exec-warm 单次往返延迟预算(P50/P95 目标设计期定;warm session 命中 vs 冷启动 fallback 分别度量)+ 锁竞争 benchmark + load/soak,作为 Tier3 PR 合并门。

### TE-ADR-6 edit 鲁棒化 + 硬 CAS(独立分量 PR)
- **决策**:`edit_file` 做**多级匹配降级**:精确 → 空白/缩进归一 → 锚点/模糊匹配 → 失败返回结构化错误(列候选/上下文)让模型重试。参考 `openclaw` `wrapEditToolWithRecovery`、aider edit 策略。
- **+ 硬 CAS**:入参 `expected_hash`(读时返回的内容 hash),写前校验当前内容 hash;不符→结构化 `stale` ToolMessage 回传(接 YOLO 自纠错)。
- **分工**:匹配降级管"模型自己对不上"(可用性),CAS 管"别人并发改了"(正确性),两者都要。三家参考最强也只 advisory stale(hermes),硬 CAS 是 helix 差异化。

## 2. 风险 / 约束

- **bash 安全**:边界 = gVisor 沙箱 + workspace 限定 + 超时 + 审计 emit + 不可逆门控;**绝不在 orchestrator 宿主跑 bash**。
- **跨副本锁正确性**(命门):必须 PG advisory xact lock,严禁退回 in-process 糊弄(§0.3 已证 in-process 不安全)。RED/GREEN 真 PG 集成测验证并发写不交错。
- **性能**:文件原语多一跳 supervisor;读 lock-free + keep-warm 缓解;load/soak 进验收门。
- **审计脱敏**:CodeQL py/clear-text-logging / log-injection——args/路径脱敏,不 log secret-命名值/请求派生值([[feedback_codeql_clear_text_logging_secret_name]] / [[feedback_codeql_log_injection_request_taint]])。
- **双 Literal**:动 `protocol/audit.py` 须 protocol + control-plane 两处([[project_audit_literal_drift]])。
- **零热路径回归**:不动现有 `scheduling.py`/`asyncio.gather` 既有语义,只增 `irreversible` 档;现有调度测全绿。

## 3. Stream 切分

### Stream TE — 引擎层(按办公杠杆排序)

**P0 前置**
- **TE-0 设计先行**(本 PR):本文档 + `ITERATION-PLAN.md` backlog。
- **TE-1 工具元数据**:`ToolSpec` 加 `side_effect: Literal["read_only","reversible","irreversible"]`(default 由 `is_read_only` 派生)+ `idempotent: bool`;保留 `is_read_only`/`path_args`;现有 builtins 声明。纯增量 + 单测。
- **TE-2 工具级审计 emit**:`builder.py` `_dispatch_tool` 接 `TOOL_CALL`/`TOOL_BLOCKED`(复用 `AuditEntry`/`AuditLogger`/`AuditAction`,仿 `_emit_run_end_audit`);脱敏 args/path/耗时/outcome/tenant/user。
- **TE-3 可观测补全**:per-tool 指标 + Langfuse span + trajectory 富化(TE-ADR-5)。
- **TE-4 side_effect 门控**:扩 `scheduling.py` `irreversible`→串行;`tools/eval/hitl.py` 硬编码 gated_tools 换成按 `side_effect` 自动 gate。
- **TE-5 bash 工具**:TE-ADR-1,经 supervisor 沙箱内。

**P1**
- **TE-6 deferred registry + find_tools**:TE-ADR-4。

**P2(押后保留)**
- **TE-7 workspace 文件原语**:`read_file`/`write_file`/`list_dir`(基础 exact write),走 exec-warm 通道(TE-ADR-2),沙箱内 realpath confinement 防越界,声明完整元数据(`side_effect`/`path_args`/`is_read_only`);原子写(tmp+`os.replace`)。
- **TE-8 per-workspace 排他写锁**:TE-ADR-3,写(文件原语 + bash)经 PG advisory xact lock(key=`{tenant}:{user}`,orchestrator DB 事务横跨写 exec),读无锁。
- **TE-9a edit 精确 + 硬 CAS**:`edit_file` 精确匹配 + `expected_hash` 硬 CAS;`stale`/`no_match`/`ambiguous` 结构化错误(TE-ADR-6 第一半)。
- **TE-9b edit 模糊降级**:精确→空白归一→锚点/模糊 多级降级链 + 失败结构化候选(TE-ADR-6 第二半,紧接 TE-9a)。
- **TE-10 性能验收门**:TE-ADR-5 性能部分(基线/锁竞争/load-soak/SLO)。

### Stream OFFICE — 办公能力包(应对办公 70%,可与 TE 并行)
- **OFFICE-0 设计先行**:办公场景盘点 + Skill/连接器清单。
- **OFFICE-1 沙箱镜像办公依赖**:pandas/openpyxl/python-docx/python-pptx/pypdf/pdfplumber/Pillow + 系统二进制(pandoc/libreoffice-headless);独立可先行。
- **OFFICE-2 seed MCP catalog**(复活推迟的 W6):Gmail/Outlook/Slack/Drive/Notion 等官方连接器进平台目录(复用 Stream W catalog 基建)。
- **OFFICE-3 办公 Skill 打包**(复用 Stream X):读文档/出报告/做 PPT/数据分析模板。

## 3.1 依赖与顺序

```
TE-0 ─> P0(TE-1→TE-2→TE-3→TE-4→TE-5) ─> P1(TE-6)
                                          └> P2(TE-7→TE-8→TE-9a→TE-9b, TE-10 perf 门)
Stream OFFICE: OFFICE-0 ─> (OFFICE-1 ∥ OFFICE-2 ∥ OFFICE-3)   // 可与 TE 并行;OFFICE-3 接 TE-5/7 更顺
```

## 4. CI / 约束
- 预计 **无 DB migration**(`TOOL_CALL`/`TOOL_BLOCKED` 已在 `AuditAction`;`side_effect` runtime;锁用 `pg_advisory_xact_lock` 无需建表)。OFFICE-1 改沙箱镜像 Dockerfile,非 DB。
- mypy 作用域见 [[reference_ci_lint_type_test_scopes]];pytest `-m "not integration"`,锁/CAS/性能并发用例进 integration;CodeQL 审计脱敏;push 前 preflight(ruff/pre-commit,[[feedback_ruff_strict_lint_traps]]/[[feedback_uv_lock_and_precommit_ruff]])。
- 每 PR 零技术债收尾 + 同步 ITERATION-PLAN(checkbox+PR#,[[feedback_iteration_plan_sync_after_ship]])。

## 5. Verification(分阶段)
- **TE-1**:ToolSpec 新字段默认派生正确;现有调度测全绿(向后兼容)。
- **TE-2/3**:工具调用产生 `TOOL_CALL` 审计行(脱敏)+ Prometheus 计数 + trajectory 含 exit_code/读写集;失败产生 `TOOL_BLOCKED`。
- **TE-4**:`irreversible` 工具批次强制串行;`side_effect=irreversible` 自动触发 approval gate(非硬编码列表)。
- **TE-5**:bash 在沙箱内执行、限 workspace、越界/超时被挡、审计有记录。
- **TE-6**:大量工具下默认只暴露核心 + `find_tools`;`find_tools` 三查询命中并 promote 后可调用;token 占用受控。
- **TE-7**:read/write/list 经 exec-warm 作用于 workspace;沙箱内 realpath confinement 拒越界(`..`/symlink/绝对路径);写原子(tmp+replace);锁 key 归一化 `./x`==`x`。
- **TE-8**(真 PG 集成测):并发写同一 workspace 经 per-workspace advisory lock 串行、不交错;bash 与文件写互斥(同一 workspace 锁);读/list 不取锁、不被写阻塞(原子 rename 保读一致快照)。
- **TE-9a**:edit 精确命中;`expected_hash` 不符→`stale`{current_hash};0 命中→`no_match`;>1 命中→`ambiguous`{count};模型据此重读重改。
- **TE-9b**:精确失败时空白归一/锚点/模糊多级降级命中;全失败返回结构化候选位置。
- **TE-10**:文件原语 P50/P95 达 SLO;锁竞争 benchmark 无死锁/饥饿;load/soak 稳定。
- **OFFICE**:沙箱内 import 办公库成功 + pandoc/libreoffice 可调;catalog 出现办公连接器且租户可实例化;办公 Skill 可绑定生效。

**完成 = helix TOOLS 引擎产品级**(治理/隔离/审计/并发正确性齐备,且办公工具面补足)。
