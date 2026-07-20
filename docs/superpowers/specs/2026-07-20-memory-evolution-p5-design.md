# P5 —— 记忆模块演化能力(检索质量 + 时间有效性)设计

> 日期:2026-07-20。Backlog P5。基于 2026-07-20 记忆模块能力评估(vs deer-flow / hermes / LongMemEval / Zep / Generative Agents)。
> 交付:**一个 spec,分 P5a(检索质量)→ P5b(时间模型+溯源)两阶段**,P5b 内部可能再拆 2-3 PR。

## 背景

移除 impersonation、B2/B3 收尾后,记忆模块的**检索/精排/隔离/可观测/安全**五维已领先两个参照项目(deer-flow 记忆更弱,hermes 内置记忆是两个 markdown 文件)。但用 LongMemEval 五大能力量,我们在**"记忆随时间演化"**这一类有系统性缺口:

- **Temporal Reasoning ❌**:只有 recency 衰减,无事实时间线——"三个月前住哪"答不出。
- **Knowledge Updates ⚠️**:reconcile 有 UPDATE/DELETE,但**破坏性覆盖**,旧值改写丢历史。
- **Abstention ❌**:读时 verify fail-open,检索失败保留全部 → 过度召回,不 abstain。
- **强化断裂**:衰减锚 `last_used_at` 检索命中后从不刷新,且无 `access_count`;consolidator 清除保护"从未被检索"判定**恒真**(高频召回记忆也进清除候选)。
- **importance 打了分不进排序**:只用于写过滤,召回排序只有 `relevance × decay`,Generative Agents 三信号砍掉一个。
- **无 run 级溯源**:只有 `source_thread_id`。

## 范围决策(已与用户确认)

1. **一个 spec 分 P5a → P5b 两阶段**,不拆独立 epic:P5a 的 access 强化改检索排序、P5b 的 bi-temporal 改检索过滤,**动同一段 `retrieve` 代码**,分独立 epic 会导致第二波 rebase 第一波。统一规划检索演进(像 B2/B3)。
2. **bi-temporal 只搬时间模型,不含 graph**:四时间戳 + fact 版本链 + 时间旅行检索,全打在现有扁平 fact 行上。graph(实体关系图)保持架构级判断,单列 backlog。
3. **procedural 记忆不动**:skill 系统已有独立 curator/consolidator/eval/版本/审批,比"记忆里的 procedural"成熟,不合并、不交叉引用。
4. **per-fact TTL 融入 bi-temporal 主动复核**(不单列)。
5. **correction 信号复用 flush 抽取产 invalidate**(不搞独立正则/LLM),时机 flush。

## 现状锚点(代码,写 plan 依据)

- 表 `memory_item` / ORM `MemoryItemRow`(`models/memory_item.py`)。现有列:id/tenant_id/user_id/kind/agent_name/content/embedding/content_hash/source_thread_id/importance/confidence/created_at/**last_used_at(已存在,NOT NULL default now)**/deleted_at/content_tsv/status/consolidated_into/consolidated_from/last_reviewed_at/review_flagged_at。RLS(migration 0017)双闸 tenant+user。
- 检索排序(`memory/sql.py`):`_vector_retrieve`(193-235)SQL cosine 粗排 limit → Python `similarity(1−dist/2) × _decay_for(last_used_at, created_at)` 重排(227-234);`_hybrid_retrieve`(237-306)vector+keyword 双路 → RRF fuse → `score × _decay_for` 重排(298-305)。`_decay_for`(41-54)锚 `last_used_at` 回退 `created_at`,`temporal_decay_factor`(`common/search/decay.py`)= `0.5+0.5·2^(−age/30d)` floor 0.5。
- 召回节点(`graph_builder/memory.py` `make_memory_recall_node` 410-513):`recall_limit=max(top_k,20)`(459)→ `embedder.embed([task])`(461)→ `retrieve`(462)→ rerank(476)→ MMR(485)→ verify(496)→ redact(509)。
- reconcile(`memory.py` `_reconcile_and_apply` 576-674):对每个新抽取 item 检索 cosine≥0.80 近邻 → LLM 判 ADD/UPDATE/DELETE/NOOP;`_apply_update`(677-696)调 `update_content`(**原地改 content**),`_apply_delete`(699-712)调 `soft_delete`。
- 抽取核心(`memory.py` `flush_messages_to_memory` 715-855):run-end writeback + 压缩前 flush + DLQ 共用,`_EXTRACT_SYSTEM` prompt 抽 fact/episodic + importance/confidence。
- consolidator never-used 判定(`sql.py` `list_purge_candidates` 588-600):`last_used_at ≤ created_at + INTERVAL '1 minute'` —— **恒真 bug 源**。
- 指标(`common/uplift_metrics`):`record_memory_retrieval/rerank/mmr/verify/reconcile/redacted/drift`。
- aux LLM 用途标记:`LLM_SPAN_PURPOSES`(common 单源)含 `memory`,query 改写复用。

---

# P5a —— 检索质量(先,现有架构内,低风险)

## ① access 强化

**现状**:`last_used_at` 列已存在,`_decay_for` 已锚它,但**检索命中后从不刷新**;无访问计数。

**改动**:
- schema 加 `access_count int NOT NULL server_default 0`(migration)。
- 检索命中后**异步 fire-and-forget**(不阻塞返回,不进 `token.run_cancellable` 主路径):对**最终注入的 top_k**(非 wide recall 20 全部)`UPDATE memory_item SET last_used_at=now(), access_count=access_count+1 WHERE id = ANY(:ids)`。落点:召回节点 `make_memory_recall_node` 拿到最终 `memories` 后(redact 前后皆可,用原始 id)触发一次批量 bump;新增 `MemoryStore.bump_access(ids)` 方法(sql 批量 UPDATE / in-memory 对应)。异步失败仅日志,不影响召回。
- 衰减叠频次 boost:排序 key 从 `similarity × decay` 改 `similarity × decay × freq_boost`,`freq_boost = min(1.5, 1 + log10(1+access_count)·0.1)`(10 次≈1.1×、100 次≈1.2×,cap 1.5× 防老热霸榜)。新增 `_freq_boost(access_count)` 纯函数(放 `common/search/decay.py` 邻 `temporal_decay_factor`,便于单测+复用两处排序)。两处排序(_vector 227-234 / _hybrid 298-305)同步改。
- consolidator never-used 判定改精确:`list_purge_candidates` 的 `last_used_at ≤ created_at+1min` → `access_count = 0`(顺手修恒真 bug;in-memory 同步)。

**测试**:bump 后 last_used_at/access_count 变化;freq_boost 单调+cap;排序纳入频次;never-used 用 access_count(高频召回 fact 不再进清除候选)。

## ② importance 进检索排序

**现状**:importance 是 MemoryItem 字段,只用于 `write_min_importance` 写过滤,召回排序不含。

**改动**:两处排序 key 叠 `w(importance)`:`final = similarity × decay × freq_boost × w`,`w = 1 + (importance−0.5)·0.4`(importance 0.5 中性=1×、1.0=1.2×、0.0=0.8×)。importance 是微调,relevance 仍主轴。新增 `_importance_weight(importance)` 纯函数(同 decay.py)。无 schema 改。

**测试**:同 similarity 下高 importance 排前;w 在 [0.8,1.2];importance 不压倒强相关(高 importance 弱相关仍排低相关强后)。

## ③ query 改写

**现状**:召回节点直接 `embedder.embed([task])`,task = 最后一条 human 消息原文。含指令的长消息污染检索向量。

**改动**:embed 前一次轻量 aux LLM 调用(带 `memory` purpose span)把 task 改写成检索问句 + 剥指令。新增 `_rewrite_query(llm, task, token)`(仿 `_verify_memories` 的 best-effort 形状)。**fail-open**:改写失败/超时/空 → 用原文。可配置开关(平台或 agent 级,默认开)。改写后的问句同时喂 embed + hybrid 的 `query_text`。

**测试**:改写成功用问句;失败 fail-open 用原文;指令泄露被剥(参照 hermes `_INSTRUCTION_LEAK_RE` 思路,但判定交 LLM prompt)。

## ④ abstention 阈值门

**现状**:wide recall → rerank → MMR 总凑够 top_k,哪怕全不相关;verify fail-open 反而过度召回。

**改动**:精排(MMR/verify)后加一道**绝对相关性阈值门**:若最高 relevance(用 cosine similarity,稳定可解释)< 阈值 → 返回空(agent 得"无相关记忆")。落点:召回节点在 redact 前。阈值**可配置、默认保守**(低阈值宁可多召回),先用 `record_memory_retrieval` 埋点观察 similarity 分布再收紧;新增 `record_memory_abstain()` 指标记录触发次数。**不碰 fail-open**——verify 故障仍保留全部,阈值门只管"真没相关的",两者正交。

**测试**:全不相关 → 空;有相关 → 正常;阈值可配;fail-open 不受影响。

---

# P5b —— 时间模型 + 溯源(后,数据模型重构,中大)

## ⑤ run 级溯源

**改动**:schema 加 `source_run_id uuid NULL`(和 bi-temental 加列合进**一个 migration**)。三写入点透传 run_id:`flush_messages_to_memory` 签名加 `run_id`,run-end writeback / 压缩前 flush / DLQ 重试三处传入(config 里 run_id 已在手边,B2 已让 run 成一等公民)。`MemoryItem` protocol 加 `source_run_id: str | None`。前端记忆 tab 从记忆跳 run 详情。

## ⑥ 完整 bi-temporal 时间模型

**新列**(migration,全 nullable 非阻塞回填):
- `valid_at timestamptz`(世界中生效,回填=created_at,抽取时 LLM 可覆盖)
- `expired_at timestamptz NULL`(世界中失效——事实本身不再为真)
- `invalid_at timestamptz NULL`(db 层被取代——被新版本 supersede)
- `supersedes uuid NULL`(指向被本行取代的旧 fact)
- `superseded_by uuid NULL`(反向)
- `expected_valid_days int NULL`(⑦ per-fact 预测窗口)

**与现有生命周期列的关系**(命门,避免语义打架):
| 列 | 语义 | 检索是否排除 |
|---|---|---|
| `deleted_at` | 用户/purge **软删**(遗忘) | 是(已有) |
| `consolidated_into` | 被巩固进 summary | 是(已有 `_retrieve_filter`) |
| `invalid_at` | **被新版本取代**(bi-temporal) | 是(新增) |
| `expired_at` | 世界中失效但无取代者 | 是(新增) |
| `status` | transient/consolidated/archived | 部分 |

`invalid_at`/`expired_at` 是**正交新维度**:失效行**保留**(可查历史/审计),只是默认检索排除。检索 where 加 `invalid_at IS NULL AND (expired_at IS NULL OR expired_at > now())`(纳入 `_retrieve_filter` 或平行)。

**reconcile 改 append-only**(核心):`_reconcile_and_apply` 的 UPDATE/DELETE 从破坏性改版本链:
- **UPDATE**(新 item 取代旧):旧行 `invalid_at=now, superseded_by=新id`;新行写入 `supersedes=旧id, valid_at=now`。`update_content` 语义从"原地改"变"关旧开新"——改 `_apply_update` + 新增 store 方法 `supersede(old_id, new_item)`(事务内原子关旧+写新)。
- **DELETE**(撤回,新 item 否定旧事实):旧行 `expired_at=now`(世界中不再为真、无取代者——与 UPDATE 的 `invalid_at` 正交),无新行。`_apply_delete` 从 `soft_delete` 改设 `expired_at`。
- **ADD/NOOP**:不变。

**时间旅行检索**:`retrieve` 加可选 `as_of: datetime | None`;传入时 where 改 `valid_at ≤ as_of AND (invalid_at IS NULL OR invalid_at > as_of) AND (expired_at IS NULL OR expired_at > as_of)`。默认 `as_of=None` = 当前有效。前端/工具可选暴露"查历史"。

## ⑦ per-fact 预测复核窗口(融入)

**改动**:抽取时 LLM 给 `expected_valid_days`(deer-flow 式逐条,五档指导),作"预测失效窗口"。staleness 主动复核从全局阈值改成:`created_at + expected_valid_days` 到期的 fact 优先被复核 LLM 确认(还对→延长/清 expected;失效→设 `expired_at`)。失效因此两条路:**被动**(reconcile/correction 检矛盾→invalid_at)+ **主动**(预测到期→复核→expired_at)。复用现有 consolidator staleness pass 的调度骨架。

## ⑧ 对话内 correction 信号 → invalidate(复用 flush 抽取)

**关键洞察**:correction 大半**已在 reconcile 管线里**——用户说"我搬家了"→ 下次 flush 抽取"用户住上海"→ reconcile 检索近邻"用户住北京"→ LLM 判 UPDATE。⑥ 把 UPDATE 从破坏性改 append-only invalidate 后,**correction 自然走版本链**,旧值失效但可查历史。

**增量**:抽取 prompt(`_EXTRACT_SYSTEM`)加一句,让 LLM 标记"本轮是否对之前信息的显式纠正",提高 reconcile 的 UPDATE 召回(否则弱纠正可能被判 ADD 产生并存矛盾)。**零额外 LLM 调用**(复用抽取)。时机 flush(与即时无实际差异:记忆注入 turn 开始冻结,本轮改下轮生效)。

---

## 迁移/回填策略

- 一个 migration 加全部新列(P5a 的 `access_count` 可并入或单独;P5b 的 6 列一批)。全 nullable 或带 server_default,非阻塞。
- 回填:`access_count=0`、`valid_at=created_at`、其余 NULL。老行天然"当前有效"(invalid_at/expired_at NULL)。
- 平台级 tenant-less 无关;`memory_item` 是 tenant+user RLS 表,新列不改 RLS。
- migration revision id ≤32 字符;down_revision 接当时最新(写 plan 时确认)。

## 检索代码演进(P5a→P5b 叠加,避免冲突)

排序 key 是单一演进点,两阶段依次叠:
- P5a 后:`similarity × decay × freq_boost × w(importance)`
- P5b 后:where 加 `invalid_at IS NULL AND (expired_at IS NULL OR expired_at>now())`;可选 as_of 分支。排序 key 不变。
两处排序(_vector/_hybrid)始终同步改;纯函数(freq_boost/importance_weight)集中 decay.py 便于单测。

## 测试策略

- 单测:decay.py 三纯函数(decay/freq_boost/importance_weight)边界+单调;reconcile append-only(UPDATE 关旧开新+版本链双向;DELETE 设 invalid_at);时间旅行 as_of 过滤;abstention 阈值门;bump_access。
- 集成(需 DOCKER_HOST):migration 链;bi-temporal 检索默认排除失效+as_of 查历史;reconcile 真库版本链;never-used 用 access_count。
- 回归:改 `flush_messages_to_memory` 签名(加 run_id)要跑 control-plane 测(经真 run_agent),非只 orchestrator;改共享 decay.py 跑全库引用。
- CI 范围:mypy(packages+orchestrator+control-plane services)、ruff 全库+format、pytest 含 tools/eval。

## PR 切分

- **P5a**(1 PR):① access 强化 + ② importance 排序 + ③ query 改写 + ④ abstention 阈值门。共享 decay.py 三纯函数 + 检索排序改 + 召回节点改。
- **P5b-1**(1 PR):⑤ 溯源 + ⑥ bi-temporal schema/migration + reconcile append-only + 检索默认过滤(不含 as_of API)。核心数据模型。
- **P5b-2**(1 PR):⑥ 时间旅行 as_of 检索 + ⑦ per-fact 预测复核 + ⑧ correction prompt 增强 + 前端记忆 tab 跳 run/查历史。
(P5b 是否拆两 PR 视 P5b-1 体量,plan 时定。)

## 不做(单列 backlog)

- **graph/关系记忆**:实体关系图 + bi-temporal 打边上,架构级,风险最高,收益需实测。bi-temporal 已在扁平 fact 上兑现 Temporal Reasoning + Knowledge Updates 主体价值。
- **procedural 记忆合并**:skill 系统旁路,治理更成熟,不合并。
- **skill↔memory 交叉引用**:价值低(skill 自有检索+飞轮,交叉引用冗余+跨隔离别扭),砍。

## 风险

- reconcile append-only 增加行数(每次纠正+1 版本行);失效行长期累积靠 retention 硬删清理(现有 90 天 sweep 覆盖 deleted_at,需确认是否也扫 invalid_at/expired_at——写 plan 时定,可能扩 retention)。
- abstention 阈值校准错会漏召回;默认保守+埋点先行。
- query 改写加 1 aux LLM 调用/检索;fail-open + 可关兜底。
- freq_boost/importance_weight 常数(0.1/0.4/cap 1.5)是初值,需 offline 观察调优;集中纯函数便于改。
