# 记忆增强流 — 设计文档(Stream Memory-Enhance)

> 状态:设计稿(PR 0)。owner 已拍方向(2026-06-25):做 ①重要性/置信度写入过滤 + ②读时验证(**默认开**)+ ④用户自助纠正 API;③知识图谱先 backlog。本文把三项 ground 到现有记忆子系统的代码,给出 PR 拆分。

## 0. 背景与定位

记忆子系统已相当完整(对标 Mem0 v2 / Zep 后剩三个真 gap):
- **已有**:四层(working/episodic/semantic/procedural)、去重(`content_hash` + 部分唯一索引)、Mem0 式演化(reconcile ADD/UPDATE/DELETE/NOOP)、时间衰减(`temporal_decay`)、混合检索 + RRF、隔离 + RLS + 审计、per-agent episodic 隔离(#808)、注入写过滤(strict threat-scan)、生命周期(transient→consolidated→archived)。
- **真 gap(本流补)**:
  1. **无重要性/置信度**:`MemoryItem` 无 `importance`/`confidence` 字段;写入无价值过滤,trivial 与高价值同等落盘。
  2. **recall 无读时验证**:recall 管线有 rerank/MMR/redaction,但无「针对当前 query 校验候选记忆是否相关/未过时」的 LLM 步骤(Mem0 称读时验证是抗污染最有效手段)。
  3. **无用户自助纠正**:`/v1/memory` 仅 list/patch/delete(治理面 admin CRUD),无「end-user 说『这条记错了』」的自助纠正语义;`DynamicContextSpec.inject_memory` toggle 在 schema 里但**无消费者**(死开关)。

**不做**:③知识图谱实体-关系(Mem0/Zep 亮点但贵 + ROI 待定,先 backlog,别盲追 SOTA)。

## 1. 现状锚点(grounded)

| 关注点 | 位置 |
|---|---|
| 协议记录 `MemoryItem` | `packages/expert-work-protocol/.../protocol/memory_item.py:28`(无 importance/confidence) |
| ORM 行 `MemoryItemRow` | `packages/expert-work-persistence/.../models/memory_item.py:23` |
| 最新 migration | `migrations/versions/0098_memory_item_agent_name.py`(新从 **0099** 起;revision id ≤ 32 字符) |
| 抽取提示 `_EXTRACT_SYSTEM` | `services/orchestrator/.../graph_builder/memory.py:70`;`parse_extracted_memories`:113 |
| 写入核心 `flush_messages_to_memory` | `graph_builder/memory.py:536`(抽取→embed→build→reconcile→`store.write`) |
| 写闸先例 | strict threat-scan `memory/sql.py:108-115`;reconcile gate `memory.py:398` |
| retrieve | `memory/base.py:53` + `memory/sql.py:156`(hybrid/RRF/decay/agent_name) |
| recall 节点 + 管线 | `make_memory_recall_node` `memory.py:253`;retrieve 调用 296;rerank 182 / MMR 222 / redaction 140;return 333 |
| 注入 prompt | `graph_builder/builder.py:492`(`_inject_memories`) |
| 控制面 API | `control_plane/api/memory.py:159`(`/v1/memory` list/patch/delete);per-user 闸 `_require_caller_user`:139 |
| RBAC `memory` 资源 | `control_plane/auth/rbac.py:42`(ADMIN/OPERATOR rwd,VIEWER r) |
| 审计动作 | `protocol/audit.py:238`(`MEMORY_UPDATE`/`MEMORY_FORGET`/…) |
| 默认开 toggle 范式 | `LongTermMemorySpec` `agent_spec.py:281`(`reconcile_writes: bool = True`:316) |
| tenant 运行期开关范式 | `tenant_config.py:67`(`memory_recall_mode`) |
| 测试 | persistence `test_{in_memory,sql}_memory_store.py`;orchestrator `test_memory_{nodes,reconcile,recall_*}.py`;控制面 `test_memory_api.py` |

## 2. ① 重要性/置信度写入过滤

### 数据模型
- migration **0099**:`memory_item` 加两列 `importance REAL NOT NULL DEFAULT 0.5`、`confidence REAL NOT NULL DEFAULT 0.5`(0–1;CHECK `BETWEEN 0 AND 1`)。回填默认 0.5(中性,不溯改既有行价值判断)。
- `MemoryItem`(`memory_item.py:28`)+ `MemoryItemRow` 加 `importance: float`、`confidence: float`。
- 语义:**importance** = 这条记忆未来被复用的价值(稀有稳定的用户事实高,一次性闲聊低);**confidence** = 抽取的确信度(明确陈述高,推断/含糊低)。两者正交。

### 计算(不加 LLM 调用)
- 扩 `_EXTRACT_SYSTEM`(`memory.py:70`)让抽取 JSON 每条带 `importance`、`confidence`(0–1)。抽取本就一次 LLM 调用(`flush_messages_to_memory:583`),**零新增成本**。
- `parse_extracted_memories`(:113)返回 `(kind, content, importance, confidence)`;**容错**:缺字段/非法 → 默认 0.5(写回是 best-effort,不能因评分缺失丢记忆)。调用点 `memory.py:590-604` 随之带上两字段。

### 写入过滤闸
- `flush_messages_to_memory`:`parse` 后、`reconcile`/`write` 前,丢弃 `importance < write_min_importance` 的条目。
- 阈值:`LongTermMemorySpec` 加 `write_min_importance: float = Field(default=0.3, ge=0, le=1)`(镜像 `reconcile_writes` 默认开范式)。0.3 = 砍掉 trivial,保守不激进;租户可调。
- **可观测**:丢弃计数走 metric(不记 content,避免 `py/clear-text-logging`;[[feedback_codeql_clear_text_logging_secret_name]]);不新增审计动作(写回路径无审计先例,保持轻)。

### 下游收益(本 PR 落字段,后续消费)
- retrieve 排序:`importance` 可并入 decay 重排(本流不改排序公式,留字段;M2+ 可选)。
- 纠正 API(④)写 `confidence=1.0`(用户断言权威)。

## 3. ② 读时验证(默认开)

### 设计
- recall 管线新增一阶段:retrieve(:296)→ rerank(182)→ MMR(222)→ **读时验证(新)** → redaction(140)→ return(333)。
- **一次批量 LLM 调用**校验**全部**候选(非每条一次):输入 = 当前 query(最后一条 human 文本,`_last_human_text:88`)+ 候选记忆编号列表;输出 = 保留的编号集(+ 可选 drop 理由)。丢弃被判不相关/已过时/自相矛盾的。
- **成本**:每次 recall +1 LLM 调用。owner 已知并拍「默认开」。用 agent 自身模型(factory 已持有 chat model 句柄;无需平台凭证)。
- **fail-open**:验证调用异常/超时 → **保留全部候选**,不阻断 recall(记忆是 best-effort,绝不能因验证失败炸 turn)。记 metric + 非敏感 log。
- **抗污染价值**:被注入污染/drift 的记忆即使过了 redaction 的模式匹配,读时验证能按「与 query 不相关 / 内容矛盾」二次拦截(纵深防御,补 `_redact_memory` 的静态规则)。

### Toggle(默认开 + 逃生门)
- `LongTermMemorySpec` 加 `verify_reads: bool = Field(default=True, ...)`(镜像 `reconcile_writes`)。
- `make_memory_recall_node`(:253)签名加 `verify_reads: bool` + 一个 `verifier`(model 句柄/可注入,便于测试 stub);`agent_factory._build_memory_nodes`(:1461)从 `long_term.verify_reads` 传入、复用已解析的 chat model。
- tenant 运行期覆盖(`tenant_config.memory_*` 范式)留 M2,本流先 manifest 级。

### 审计/可观测
- 新审计动作 `AuditAction.MEMORY_READ_REJECTED`(双 Literal 同步 protocol + control-plane,[[project_audit_literal_drift]]),按「本 recall 丢弃 N 条」聚合记一条(不逐条记 content)。或仅 metric——**取 metric + 单条聚合审计**,可追溯不刷屏。

## 4. ④ 用户自助纠正 API

### 端点
- `POST /v1/memory/{memory_id}/correct`,在 `build_memory_router()`(`api/memory.py:159`)新增。
- body:`{action: "rewrite"|"forget", content?: str}`——`rewrite` 改写内容(必带 `content`),`forget` 标错删除(软删)。
- 复用 store `update_content`(`sql.py:340`)/ `soft_delete`(:375);`rewrite` 重新 embed(同 PATCH:230 范式)。

### 语义(区别于既有 PATCH)
- PATCH = 治理面 admin 改任意字段;**correct = end-user 对自己记忆的权威纠正**:
  - **per-user 闸** `_require_caller_user`(:139)——只能纠正自己的(machine principal 403)。
  - `rewrite` 设 **`confidence = 1.0`**(用户断言为真)+ 记纠正出处(`source_thread_id` 留空或标 `user-correction`)。
  - 新审计动作 **`AuditAction.MEMORY_CORRECT`**(双 Literal 同步)。
- RBAC:复用 `require("memory", "write")`(已 ADMIN/OPERATOR;OPERATOR=普通员工可纠正自己的,符合 per-user 闸)。

### 前端(同 PR 或紧随)
- 记忆列表页(`admin-ui` 现有 Memory 页)加「纠正」操作:改写弹框 / 「记错了」删除;列展示 `importance`/`confidence` 徽章。接线点 [[reference_admin_ui_wiring_touchpoints]];aria-label 防 axe critical [[feedback_admin_ui_form_aria_label_axe]];envelope 对账 [[feedback_envelope_vs_raw_contract_check]]。

## 5. PR 拆分

- **M-1(本 PR,PR 0)** 设计文档。
- **M-2** ①写入过滤:migration 0099 + 协议/ORM 两字段 + 抽取提示评分 + `parse` 扩 + write-filter 闸 + `write_min_importance` toggle + 测试(round-trip 两字段 / 低分丢弃 / 缺字段默认 0.5)。**模型先对**。
- **M-3** ②读时验证:recall 新阶段 + `verify_reads` toggle + factory 接线 + fail-open + `MEMORY_READ_REJECTED` 审计/metric + 测试(stub verifier 丢弃 / fail-open 保留全量 / toggle off 跳过)。**live 抗污染验**(注入污染记忆 → 验证拦截)。
- **M-4** ④纠正 API:`POST /v1/memory/{id}/correct` + `MEMORY_CORRECT` 审计 + confidence=1.0 出处 + per-user 闸 + 测试;admin-ui 纠正操作 + importance/confidence 徽章。

## 6. 验证

- **M-2**:pytest 持久化 round-trip + 抽取评分单测 + 写闸单测;无 live(纯写路径,CI fake 够)。
- **M-3**:orchestrator 节点单测(stub verifier);**live**——真模型注入一条污染/过时记忆,recall 时读时验证丢弃,对照 toggle off 不丢([[feedback_ci_green_not_live_working]]:抗污染只能 live 证)。
- **M-4**:控制面 API 测(per-user 闸 403 / rewrite 改内容+confidence=1.0 / forget 软删 / 审计 MEMORY_CORRECT);admin-ui vitest + Playwright + axe。

## 7. 踩坑预防(引自 memory)

- migration revision id ≤ 32 字符 [[feedback_alembic_revision_id_32_chars]];改 deps 看 uv.lock。
- 双 Literal 漂移:`MEMORY_READ_REJECTED`/`MEMORY_CORRECT` 必须 protocol + control-plane 两处同步 [[project_audit_literal_drift]]。
- 提交前跑**完整** pre-commit(含 ruff-format),所有改动文件(含 `__init__.py`)纳入——单 ruff hook 漏 format 是栽过的坑。
- CodeQL:别 log 记忆 content/评分派生敏感名;assert 不放副作用。
- 分支先行 + footer `Co-authored-by: leyi`,不加 Claude 署名 [[feedback_branch_first_commit_convention]]。
- 默认开新增 LLM 调用是真延迟/成本——`verify_reads` 默认 True 已 owner 拍板,但实现必须 fail-open + 可关。
