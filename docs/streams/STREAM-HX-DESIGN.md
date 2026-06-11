# Stream HX — Harness 强化（设计先行）

> **背景**：2026-06-10 完成 helix harness 十维能力评估（`docs/research/2026-06-10-helix-harness-capability-assessment.md`），用户拍板把全部非强项做强，按 Wave 严格顺序推进（Wave 1 零债收完再进 Wave 2）。每条子项设计先行 + 零债收尾（Stream CM 同节奏）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条子项 PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **横切公理**（HX-12/13 立项时锁定，对全 Stream 生效）：① 不存在 drop core tool / drop 历史真相的代码路径（视图级裁剪 ≠ 状态删除）；② fail-open——基础设施故障的代价只能是多花 token，绝不能是少能力；③ config 防御解析（clamp / safe default，不 raise）。
>
> **本文件状态**：HX-1（真 tokenizer + 长上下文阈值参数化，§2）+ HX-2（用户反馈→学习闭环，§3）+ HX-3（run 级瞬态故障自动重试，§4）+ HX-4（可观测补强，§5）详设已锁定。Wave 2 各条开工时追加章节。

---

## 1. 范围 & 边界

| ID | 评估维度 | Gap | 交付 | 详设 |
|----|---------|-----|------|------|
| **HX-1** | ③ 上下文工程 | `len//4` 估算漂移（CJK 严重低估）+ `context_window` 不随目录解析 + E.3 遗留 8K 默认裁剪 | TokenEstimator 协议 + tiktoken 默认实现 + 目录解析 + 遗留默认值退役 | §2（本文） |
| HX-2 | ⑦ 学习闭环 | 👎 无学习消费者（断点精确化见 §3.1） | rollback gate 接 👎 + 记忆 review 标记 + feedback consumer worker | §3（本文） |
| HX-3 | ⑧ 容错 | run 级瞬态故障无自动重试 | 瞬态分类 ∧ replay-safe 守卫 → 重试 1 次 | §4（本文） |
| HX-4 | ⑨ 可观测 | approval 队列 gauge / checkpoint 延迟 / run_id 结构化贯穿缺（工具延迟与 run 成功率判定过期，见 §5.1） | gauge + checkpoint 计时 wrapper + run_id contextvar + recording rules 同步 | §5（本文） |
| HX-12/13 | ② 工具面 | 工具披露 2.0（应用层 + 厂商原生档） | 见 ITERATION-PLAN Wave 2 定义 | 开工时追加 |

Wave 1 顺序：HX-1 → HX-2 → HX-3 → HX-4（HX-1 的 estimator 是 HX-12 阈值逃生门的前置件；HX-4 的指标骨架吃 HX-1 的 drift 数据）。

---

## 2. HX-1 — 真 tokenizer + 长上下文阈值参数化

### 2.1 现状取证（2026-06-11，main@b9f4779）

| 事实 | 证据 | 判定 |
|------|------|:----:|
| 估算器 A：`estimate_tokens` = `total_chars // 4`，multimodal block 感知 | `orchestrator/context/compressor.py:127-165` | 弱（CJK 低估 ~2.5-3×） |
| 估算器 B：`default_token_estimator` = `len(str(content)) // 4`，独立重复实现 | `helix-runtime/.../middleware/dynamic_context.py:26-38` | 弱 + 重复 |
| 估算器 B 已有注入缝 `token_estimator` 字段，但 factory 从不注入 | `dynamic_context.py:72`、`middleware_assembly.py:143-156` | 缝在、未接 |
| 压缩/滑窗阈值已按 `context_window × threshold_pct` 参数化 | `compressor.py:290`、`working_window.py:145`、`agent_factory.py:510/524` | **机制已在**（评估报告"缺失"判定部分过期） |
| `ModelSpec.context_window` 默认 200_000，**不与目录联动** | `agent_spec.py:152`；目录有真值（qwen3.7-max/deepseek-v4 = 1M，gpt-5.5 = 128K） | 真 gap ①：配 1M 模型不手动覆写就按 200K 跑 |
| **always-on** `DynamicContextMiddleware` 默认 `max_turns=20 / max_tokens=8000`，每次 LLM 调用裁剪 LLM-facing 视图 | `middleware_assembly.py:45-46`、`builder.py:418-423`、`ContextCompressionPolicy.max_turns/max_tokens`（`agent_spec.py:479-480`） | 真 gap ②：**E.3（M0 naïve trim）遗留默认值**——working_window/compressor 在 140K 阈值保住的历史，最后一跳被裁到 ~8K/20 条 |
| tiktoken 已在仓内依赖树（control-plane knowledge chunking，`cl100k_base` 做 chunk sizing） | `control-plane/pyproject.toml:50`、`knowledge/chunking.py:36-44` | 引入零新依赖族 |
| `tools/` 与 control-plane 无 `estimate_tokens`/`context_window` 消费 | 全仓 grep | sweep 面收敛在 orchestrator + helix-runtime + protocol |

**gap ② 展开**：`max_turns=20` 数的是**消息条数**（非用户轮次）——一轮 ReAct 含 10 次工具调用就是 20+ 条消息，单轮即触顶；`max_tokens=8000` 两个 4K 工具结果就满。SystemMessage 豁免（压缩摘要、recall 注入存活），但普通对话尾巴每调用只剩 ~8K。这意味着五层上下文级联（滑窗→压缩→外部化→……）在默认配置下被一个 M0 时代的兜底裁剪静默架空——评估报告说"1M 模型与 200K 模型同一套阈值参数"，实况更糟：**所有模型实际每调只见 ~8K**。

### 2.2 tokenizer 选型对比

| 方案 | 准确性 | 成本/风险 | 判定 |
|------|--------|----------|:----:|
| **A. tiktoken `o200k_base` 统一近似** | 全厂商 ±10% 量级；CJK 从 ~2.5-3× 低估收敛到 ~±15% | 已在依赖树；Rust 实现快；首用下载 BPE 文件（~4MB，需网络） | **选** |
| B. per-provider 真 tokenizer（HF tokenizers） | 开源模型精确 | 9 provider 异构；anthropic 无公开 tokenizer；运行时下载 vocab 不可接受；依赖重 | 否 |
| C. 改良启发式（CJK 字符分类加权） | 比 chars//4 好但仍 ±20%+ | 零依赖 | 否（A 成本同样低） |
| D. provider count API（anthropic `count_tokens`） | 精确 | 每 turn 多次网络调用进热路径 | 否 |
| E. usage 真值校准（EMA 比例修正） | 自适应收敛 | 冷启动无数据；状态管理复杂 | 不做实现，**留数据**（§2.5 drift 指标喂未来校准） |

`o200k_base` 而非 chunking 现用的 `cl100k_base`：新版编码对 CJK 压缩率明显更好（更贴近 qwen/deepseek 等国产模型 BPE 的量级）；chunking 的 sizing 用途不动（surgical）。

### 2.3 PR1 详设 — 估算统一 + 真分词

**新模块** `packages/helix-runtime/src/helix_agent/runtime/tokens.py`（runtime 包：middleware 在此、orchestrator 已依赖 runtime，依赖方向成立）：

```python
class TokenEstimator(Protocol):
    def count(self, text: str) -> int: ...

class CharTokenEstimator:        # 现状语义封装：max(1, len // 4)
class TiktokenEstimator:
    # 惰性 get_encoding("o200k_base")——首次 count 时加载；
    # 加载或编码任何异常 → 一次性 WARNING + 永久回落 chars//4（fail-open 公理：
    # 离线环境拿不到 BPE 文件时行为 = 现状，绝不 raise 进热路径）
    # 内置 bounded LRU（maxsize=4096，按 text 哈希）——estimate 每 turn
    # 对全消息列表多次调用，append-only 前缀全是缓存命中

def flatten_message(msg: BaseMessage) -> str: ...
    # compressor._message_to_text 上移至此（block 感知版本），两个消费层统一口径
def estimate_messages(messages, estimator) -> int: ...
def default_estimator() -> TokenEstimator:   # 进程级单例（vocab 只加载一次）
```

**接线（三消费点，一个真相源）**：

1. `ContextCompressor` / `WorkingWindow` 增 `estimator: TokenEstimator | None = None` 字段——**dataclass 默认 None = chars//4 现状**（既有单测零网络零变更），factory 统一注入 `default_estimator()`（生产真分词）。`compressor.estimate_tokens` 模块函数保留兼容签名、增可选 estimator 参。
2. `DynamicContextMiddleware`：用现成 `token_estimator` 缝（`dynamic_context.py:72`），`middleware_assembly._dynamic_context` 增 estimator 参数，factory 传入（适配 `flatten_message` + `count`）。
3. `helix-runtime` pyproject 增 `tiktoken>=0.8,<1`（与 control-plane 同约束；注意 uv.lock 漂移）。

**可观测（零债项）**：`TokenUsageMiddleware`（after_llm_call，usage_metadata 真值已在手，`prompt_messages` 已在 payload）增 estimator 注入，发 `helix_hx_token_estimated_total` counter（估算 prompt token 累计；本地 cache hit 跳过）。漂移比在 PromQL 侧求：`rate(helix_hx_token_estimated_total) / rate(helix_llm_token_usage_total{type=~"input|cache_.*"})`——既是 HX-1 的验收数字（上线后看比值是否从 ~0.4（CJK 低估）收敛到 ~1.0），也是 §2.2-E 未来校准的数据源，并入 HX-4 指标族。**实施期修正**：原设计为 ratio histogram，仓内指标公约（`helix_histogram` 强制 `_seconds` 后缀）保留直方图给时长类，改 counter 对零公约破坏。

### 2.4 PR2 详设 — 长上下文阈值参数化（含遗留默认值退役）

**① `context_window` 目录解析**：

- `ModelSpec.context_window: int | None = None`（原 `int = 200_000`；显式正整数校验保留）。
- factory 新增解析助手：显式值 → 尊重；`None` → `catalog_entry(provider, name).context_window`；目录外或目录条目无值 → 200_000 兜底（现状不变）。`agent_factory.py:510/524` 两处消费改走解析结果。
- 效果：qwen3.7-max / deepseek-v4 / gpt-5.5 等目录模型**自动**拿到正确窗口（1M 模型压缩阈值 140K→700K），manifest 不用每个手抄目录数字；显式覆写语义不变。
- sweep：协议变更全仓 grep `.context_window`（含 admin-ui SDK/manifest 编辑面、doubles——[memory:protocol-sweep-includes-tools-eval]，当前取证 tools/eval 零消费，实施期复扫）。

**② E.3 遗留默认裁剪退役**：

- `ContextCompressionPolicy.max_turns / max_tokens` → `int | None = None`（原 20/8000）。
- `middleware_assembly._dynamic_context`：两者皆 None → **不注册** `DynamicContextMiddleware`（always-on 三件套变两件套）；任一显式设置 → 照常注册（视图级裁剪变 opt-in，middleware 类本身零改动）。`_DEFAULT_MAX_TURNS/_DEFAULT_MAX_TOKENS` 镜像常量删除。
- 理由：该兜底是 M0（五层级联落地前）的 naïve trim；如今滑窗（CM-2）+压缩（L2）+工具结果外部化（CM-5）已在 `context_window` 比例阈值上分层接管，8K 绝对值默认裁剪与级联语义冲突且静默架空之（§2.1 gap ②）。保留 opt-in 是因为"便宜的每调硬上限"对特定 agent（如固定小窗任务型）仍是合法需求。

### 2.5 行为变更清单（设计 PR 显式过堂）

| # | 变更 | 影响 | 性质 |
|---|------|------|------|
| 1 | CJK 对话压缩/滑窗触发点提前（估算从低估 ~2.5-3× 修正） | 压缩在**正确**的填充率触发；中文重对话不再逼近真实窗口才动手 | 缺陷修正 |
| 2 | 目录内长窗口模型阈值自动放大（qwen3.7-max：140K→700K 触发） | 长对话晚压缩、prompt 变长、单调成本上升——**这正是买 1M 窗口的目的** | 缺陷修正 |
| 3 | 默认配置下每调 8K/20 条视图裁剪消失 | LLM 实际看到滑窗+压缩管理后的完整视图；prompt 均值上升 | **E.3 遗留默认值退役**（显式配置不受影响） |

三条都是"能力修正"方向（[memory:complete-not-minimal]），代价是 token 成本可见上升；G.9 token_usage 计量在位，成本可观测。

### 2.6 边界（不做）

- 不做 per-provider 精确 tokenizer / 不做运行时 vocab 下载管理（§2.2-B 否决）。
- 不做估算自动校准（留 drift 数据，需求成熟再立项）。
- 不动 control-plane chunking 的 `cl100k_base`（用途是 chunk sizing，非本链路）。
- 不动 CM-N5 eval 路径（自带 client，零 `estimate_tokens` 消费，基线可比性不受影响）。
- `ContextOverflowError` 语义、压缩算法本体、滑窗算法本体零改动——只换"尺子"和"尺度"。

### 2.7 测试

- **PR1**：TiktokenEstimator 加载失败回落（monkeypatch import/get_encoding 抛错→chars//4 + 单次 warn）；LRU 命中（同文本二次 count 不再编码）；CJK 健全性（中文串 count 显著 > chars//4）；compressor/working_window 注入 fake estimator 阈值行为矩阵；middleware 适配器接线；drift histogram 发射。CI 无网络风险规避：单测全部走 fake/Char estimator，TiktokenEstimator 真加载仅 1 条带 skip-on-failure 的冒烟。
- **PR2**：context_window 解析矩阵（显式/目录命中/目录条目无值/目录外）；factory 把解析值传 compressor+window 断言；policy 双 None → middleware 不注册、显式设置 → 注册且裁剪行为不变；既有 manifest fixture 全量回归（显式 200_000 的 fixture 行为逐字节不变）。

### 2.8 Mini-ADR

- **HX-A1 估算统一注入**：三消费点（compressor/working_window/dynamic_context）统一 `TokenEstimator` 协议注入；dataclass 默认保 chars//4（测试零网络），factory 注入真实现（生产真分词）。
- **HX-A2 o200k_base 统一近似**：不做 per-provider tokenizer（9 provider 异构 / anthropic 无公开 tokenizer / 运行时 HF 下载不可接受）；任何加载/编码失败一次性 warn + 永久回落 chars//4（fail-open 公理）。
- **HX-A3 bounded memo**：估算进热路径（每 turn 多次 × 全消息列表），LRU maxsize=4096 按 text 哈希；append-only 消息前缀天然高命中。
- **HX-A4 context_window 目录解析**：`ModelSpec.context_window` 默认 `None` = build 期 `catalog_entry` 解析，200K 兜底；显式值永远优先。manifest 与目录的单一真相源关系与 CM-9/10 能力位同构。
- **HX-A5 E.3 默认裁剪退役**：`max_turns/max_tokens` 默认 `None` 即不注册 middleware；显式配置完整保留（opt-in）。视图级裁剪不再默认架空五层级联。
- **HX-A6 drift 可观测**：`helix_hx_token_estimated_total` counter（估算 prompt token 累计）进 TokenUsageMiddleware，与既有 `helix_llm_token_usage_total{type=input/cache_*}` 真值在 PromQL 求漂移比；验收数字 + 未来校准数据源，归 HX-4 指标族。（实施期修正：原 ratio histogram 违反"直方图仅时长"公约，改 counter 对。）

### 2.9 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | 本文件 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1 | `runtime/tokens.py` + 三处注入 + tiktoken 依赖 + drift 指标 + 测试 | §2.7-PR1；全链回归 |
| PR2（收尾） | context_window 目录解析 + 遗留默认裁剪退役 + sweep + 测试 | §2.7-PR2；全链回归；零债 6 条 |

---

## 3. HX-2 — 用户反馈→学习闭环

### 3.1 现状取证（2026-06-11，main@6a0d488）

| 事实 | 证据 | 判定 |
|------|------|:----:|
| G.6 feedback API/store：👍/👎+comment 入库（thread 级或 turn 级），无任何"已处理"游标 | `api/feedback.py`、`feedback_store.py`（insert/list_for_thread 仅两方法） | 在 |
| J.12 curation_worker **已消费 feedback**：轨迹×feedback join → `curation_candidate`（signal=negative_feedback/positive_feedback/failed_outcome，含 thread/agent/version/trajectory 归因） | `curation_worker.py:1-24` | 在（评估"无消费者"判定**部分过期**） |
| SE worker 消费 candidate，但 `EVOLVE_SIGNALS = {positive_feedback, failed_outcome}` —— **negative_feedback 显式排除**（注释：SkillGen contrastive induction 取成功型 + 失败 outcome） | `skill_evolution_worker.py:37,139` | **真断点 ①**：👎 candidate 零消费者 |
| SE-7d-1 `skill_run_usage`：(thread_id, skill_id, skill_version, outcome) 归因在位；SE-7d-2/3 rollback gate 按 per-version 窗口做单边二项检验，cancelled 剔除 | `skill/base.py:370-394`、`skill_rollback.py`、`skill_rollback_gate.py:68-74` | 在（👎 的正确接入点） |
| `MemoryItem.source_thread_id` 在位（👎 thread → 关联记忆可查）；consolidator SUB-PASS 2 单条复审通路在位（U-37：durable/noise 分类 + `mark_reviewed`） | `memory_item.py:43`、`memory/base.py:216-230`、`memory_consolidator.py` | 在 |
| 记忆条目无任何"用户反馈→复审"通道；consolidator 候选仅 aged-lone-transient | `list_purge_candidates` 三过滤 | **真断点 ②**：👎 不触达记忆 |
| `curation_candidate.status` 是**人工 review 生命周期**（PENDING=等人工，curation API promote 检查 PENDING）；J.12 对已存在 candidate 的 trajectory 直接 pre-check skip | `eval_dataset.py:52-59`、`api/curation.py:230` | 约束：HX-2 不可借用该状态机，也不可依赖 candidate 及时性 |

**范围修正**：评估 ⑦"用户点了 👎 之后什么都不会发生"在 J.12 之后已不全对——👎 会物化为 curation candidate（eval 数据集人工路径）。真断链精确化为两条：**① negative_feedback candidate 没有自动学习消费者**（SE 修订侧）；**② 记忆侧零通道**。且"进 SE 修订队列"具体化：对**已晋升** skill 的修订机制本来就是 SE-7d rollback down-gate（自动归档→再蒸馏），不存在也不应新造一条"修订队列"——👎 的正确去向是 rollback 评分窗口。

### 3.2 设计

**① skill 侧 —— rollback gate 查询时 join 👎（拉取式，零协议变更）**

- `SkillStore.skill_run_outcomes` 旁新增返回 `(thread_id, outcome)` 的方法（或扩展现签名，实施期定，含 doubles sweep）；`FeedbackStore` 新增批量查询：给定 thread 集合返回有 👎 的子集。
- rollback gate 聚合窗口时：thread 命中 👎 → 该样本 outcome 按 `failed` 计入二项检验（机器 outcome=success 被用户否决）。`cancelled` 剔除规则不变。
- **不改写 `skill_run_usage` 行**（归因行不可变，审计友好）；**不扩 `TrajectoryOutcome` Literal**（轨迹本身是 success，加值语义错位 + 全仓 sweep 代价）。👎 对绑定多 skill 的 run 会"连坐"全部版本——噪声由二项检验窗口（n_min=6 + 显著性 + 效应量地板）吸收，单个 👎 不会触发回滚。
- 决策 reason 携带 disapproved 计数（可观测）。

**② memory 侧 —— review 标记 + consolidator 复审**

- `MemoryItem.review_flagged_at: datetime | None`（协议 + 迁移 + 双 store）。
- consolidator SUB-PASS 2 增第二候选源：`review_flagged_at IS NOT NULL` 的 live transient（不要求 aged/未检索），走同一条 U-37 单条复审通路（durable → `mark_reviewed` 且清 flag；noise → soft-delete）。consolidated 父项不回炉（见 §3.3 边界）。
- `mark_reviewed` 扩展清 flag 语义。

**③ feedback consumer worker —— 直扫 feedback 表（非 candidate）**

- 新 `control_plane/feedback_consumer.py`：单副本 lifespan worker（curation_worker 同款骨架：bypass-RLS 列举 + per-tenant scope 处理 + per-row best-effort）。
- 消费源 = `feedback` 表直扫：`rating='down' ∧ processed_at IS NULL`（新列，行级戳，幂等 + 重放安全）。**不消费 J.12 candidate**：late-👎（轨迹先被扫描、candidate 已存在）被 J.12 的唯一性 pre-check 吞掉，且 candidate 依赖轨迹存在；feedback 表是无损全集。
- 每行动作：按 `source_thread_id = feedback.thread_id` 查关联记忆 → 置 `review_flagged_at`（重复 👎 重置 flag，幂等）→ 戳 `processed_at` → audit + counter。skill 侧无动作（①是 gate 拉取式）。
- 👍 零新动作（已有 golden 路径：J.12 positive candidate + SE distill）。

### 3.3 边界（不做）

- comment 文本不做 NLP/分类——只用 rating 信号。
- 👎/👍 同 thread 不做相互抵消（gate join 见 👎 即 demote；记录为已知简化）。
- consolidated 父项被 👎 关联时不回炉重审（其 transient 源已有 `consolidated_from` 反向索引，需求出现再做级联复审）。
- turn 级 feedback（`turn_seq`）暂按 thread 级处理（记忆/skill 归因都是 thread 粒度）。
- 不做实时推送——worker 周期消费（与 consolidator/curation 同步调）。

### 3.4 可观测

- `helix_control_plane_feedback_consumed_total{action=memory_flagged/noop}` counter + worker cycle error counter（既有命名纪律）。
- rollback 决策 reason 带 `disapproved=N`。
- audit：worker 对每行 👎 处理发一条（复用既有 audit emit 通道，固定字符串，不 log 请求派生值）。

### 3.5 测试

- **PR1**：store 方法 (thread_id, outcome) 双实现 + FeedbackStore 批量 👎 查询 + gate join demote 矩阵（无 👎 不变 / 👎 demote 触发 ROLLBACK / 👎 不足窗口 INSUFFICIENT / cancelled 仍剔除）+ doubles sweep。
- **PR2**：迁移（review_flagged_at / processed_at）+ flag 置位与清除 + consolidator 第二候选源（flagged 即复审、durable 清 flag、noise 删）+ worker 幂等（重复 👎 / 无关联记忆 noop 仍戳 processed_at）+ 跨租户 RLS 形态（bypass 列举 + tenant scope 处理）。

### 3.6 Mini-ADR

- **HX-B1 消费源 = feedback 表直扫 + 行级 processed_at**：J.12 candidate 有 late-👎 race（唯一性 pre-check skip）且依赖轨迹存在；feedback 表是无损全集。J.12 流程零接触。
- **HX-B2 skill 侧拉取式 join**："SE 修订队列"具体化为既有 SE-7d rollback down-gate；👎 在 gate 聚合时把该 thread 样本按 failed 计——归因行不可变、零 Literal 扩展；多 skill 连坐噪声由二项检验参数吸收。
- **HX-B3 memory 侧 review_flagged_at**：复用 U-37 单条复审通路（durable/noise 分类），flagged 候选不要求 aged；consolidated 父项不级联。
- **HX-B4 worker 骨架 = curation_worker 同款**：单副本 lifespan、bypass-RLS 列举 + per-tenant scope、per-row best-effort 不致命。
- **HX-B5 👍 零新动作 / comment 不解析**：正反馈链路已在（golden curation + distill）；NLP 收益不确定先不做。

### 3.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §3 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1 | skill 侧：store (thread_id, outcome) + FeedbackStore 批量 👎 + rollback gate join demote | §3.5-PR1；全链回归 |
| PR2（收尾） | memory 侧 + worker：两迁移 + review_flagged_at 通路 + consolidator 第二候选源 + FeedbackConsumerWorker + 接线 + 指标 | §3.5-PR2；全链回归；零债 6 条 |

---

## 4. HX-3 — run 级瞬态故障自动重试

### 4.1 现状取证（2026-06-11，main@3a59b22）

| 事实 | 证据 | 判定 |
|------|------|:----:|
| run 级零重试：`run_agent` worker 单次 `graph.astream`，任何逃逸异常 → `set_status(ERROR, error=str(exc))` + trajectory outcome=failed | `sse.py:219-495`（catch-all :458-481） | 真 gap |
| **工具异常不逃逸 tools_node**（Mini-ADR E-12）：含沙盒 acquire 失败在内全部变 error ToolMessage 喂回 LLM 自愈（+ CM-1 `tool_failures` advisory） | `builder.py:23-25`、`_dispatch_tool:1004-1062` | **取证修正 ①**：评估"沙盒 acquire 失败"不是 run 级故障类——该层已自愈，HX-3 不接管 |
| run 级瞬态故障的主类 = LLM fallback 链耗尽：`AllProvidersExhaustedError`（仅由瞬态族 `LLMError`——5xx/ratelimit/network——耗尽触发；4xx `LLMClientError` 不进 fallback 直接 raise） | `llm/router.py:130-145,172-210` | 类型可判定，零文本嗅探 |
| 工具级已有 capability-bounded retryable 规则（CM-B5）：`transient` ∧ (`read_only` ∨ `idempotent`) 才可重放 | `error_classifier.py:203-214`、`ToolSpec.side_effect/idempotent`（`registry.py:54-141`） | 规则可上移 run 级复用 |
| 续跑先例（Mini-ADR J-24）：approval resume = 同 thread checkpoint + `graph_input=None` 再 invoke；checkpoint 按 super-step 提交，失败 step 不落盘 | `api/runs.py:605-789`、`sse.py:304`（`graph.astream(graph_input, effective_config, ...)`） | 重试机制直接复用 |
| `RunCancelledError` / `MaxStepsExceededError` 各有独立终态路径（INTERRUPTED / ERROR+max_steps trajectory） | `sse.py:394,423` | 语义性终态，不属瞬态 |

**取证修正 ②——"run 零 irreversible 工具调用"守卫精确化**：评估给的守卫（全 run 无 irreversible 调用）既过保守又不精确。checkpoint 按 super-step 提交意味着：**已提交历史在续跑时绝不重放**（committed ToolMessage 不会再执行）；唯一重放窗口 = 失败那个未提交 super-step。重放内容由 checkpoint **尾部状态**完全决定——尾部是 dangling `AIMessage.tool_calls`（agent step 已提交、tools step 失败）→ 续跑重放**恰好这一批**工具调用；尾部无 dangling → 重放的是 agent_node 纯 LLM 调用，零副作用。守卫因此收敛为"尾部 dangling 批次全部 read_only ∨ idempotent"（CM-B5 同款规则），而非全 run 标记。又因取证修正 ①（工具异常不逃逸），实际逃逸到 run 级的瞬态故障几乎总是 agent_node 起源（LLM 链耗尽）——此时尾部无 dangling，重放天然安全；守卫主要防御的是"agent step 提交后、tools step 执行中进程级故障"的少数路径。

### 4.2 设计

**① 瞬态分类（注册表式，类型判定）**

- `_TRANSIENT_RUN_ERRORS: tuple[type[BaseException], ...] = (AllProvidersExhaustedError,)`——初始集只收 LLM 链耗尽（其构造保证瞬态族）。未知 `Exception` 默认**永久**：重试一个确定性 bug 没有收益预期，只有双倍成本。
- 显式不进集：`LLMClientError`（4xx 永久）、`RunCancelledError` / `MaxStepsExceededError`（语义性终态）、DB/checkpointer 故障（连接池已有一层韧性，需求出现再加 needle——注册表扩展即可，分类器不用动）。

**② replay-safety 守卫（checkpoint 尾部判定）**

- 重试前 `graph.aget_state(effective_config)` 取已提交尾部：最后一条消息是带 `tool_calls` 的 `AIMessage` 且无对应 `ToolMessage` → dangling 批次逐个 resolve `ToolSpec`：全部 `resolved_side_effect == "read_only"` ∨ `idempotent` 才放行；任一 irreversible/reversible-非幂等、或 spec 缺失（unknown name 防御）→ 不重试。
- 尾部无 dangling → 直接放行（重放 = 纯 LLM 调用）。
- `aget_state` 本身失败 → 不重试（守卫失效时保守；与 pause 检查的 graceful-degradation 同款姿态但方向相反——那边 fail-open 是多花 token，这边放行的代价可能是重复副作用，必须 fail-closed）。

**③ 重试机制（in-worker，同 run_id）**

- `run_agent` 的 stream 调用包进 `for attempt in range(2)` 循环：首轮 `graph_input` 原值；捕获瞬态 ∧ 守卫过 ∧ `attempt == 0` → emit `retry` SSE 事件（`{attempt, error_class, backoff_s}`，照常 `_persist_event` 进 run_event——历史真相公理）→ backoff 等待（用 `abort_event` 感知的 wait，abort 期间立即退出走 INTERRUPTED）→ 第二轮 `graph.astream(None, effective_config, ...)`（J-24 续跑语义）。
- 二轮再失败 / 守卫不过 / 非瞬态 → 走既有 ERROR 路径，行为逐字节不变。
- status 全程 RUNNING（重试不是新 run：SSE 流连续、RunManager 零改动、trajectory 单记录）；trajectory `metadata["retried"] = 1`（重试过的 run 可在轨迹集里筛）。
- `step_count` 不重置——MaxSteps 语义不被重试绕开（checkpoint 里的计数自然延续）。

**④ 配置（公理 ③ 防御解析）**

- env：`HELIX_RUN_TRANSIENT_RETRY`（默认 on）、`HELIX_RUN_RETRY_BACKOFF_S`（默认 10，clamp [1, 120]，解析失败回默认不 raise）。重试次数固定 1（常量不开配置面——多次重试的退避策略面是另一个需求）。

### 4.3 边界（不做）

- 工具级故障零接触——E-12 自愈分层不动，HX-3 只接管逃逸到 run 级的故障。
- 不做多次重试 / 指数退避策略面（1 次重试覆盖"独立瞬态故障"假设；连续两次失败大概率不是瞬态）。
- 不做 TIMEOUT 重试（run 超时是 deliberate bound，重试 = 双倍超时预算，语义错）。
- 不做跨进程重试（worker 进程死亡的 run 恢复是 durable-resume 范畴，K.K10 已有 TTFT 计量缝，需求成熟单独立项）。
- DB/checkpointer 瞬态故障不进初始集（见 §4.2-①；注册表一行可扩）。

### 4.4 可观测

- `helix_orchestrator_run_retry_total{outcome="recovered"|"failed_again"}` counter（recovered = 重试后到达 SUCCESS/PAUSED；failed_again = 二轮仍失败）。守卫拒绝不发 counter（拒绝即走既有 ERROR 路径，error 字段已可观测）——但 log 一条 `run_retry.guard_rejected`（含 dangling 批次工具名）。
- `retry` SSE 事件持久化进 run_event（replay 端点可见，审计链完整）。

### 4.5 测试

- 分类器矩阵：`AllProvidersExhaustedError` → 瞬态；`LLMClientError` / 裸 `Exception` / `MaxStepsExceededError` → 永久。
- 守卫矩阵：尾部 HumanMessage/ToolMessage → 放行；dangling 批次全 read_only → 放行；含 irreversible → 拒；含 reversible 非幂等 → 拒；spec 缺失 → 拒；`aget_state` 抛错 → 拒。
- run_agent 端到端（fake graph）：一次瞬态失败后成功 → 终态 SUCCESS + `retry` 事件持久化 + counter recovered + trajectory metadata.retried；两次失败 → ERROR + failed_again + error 字段为二轮异常；非瞬态 → 零重试零事件（现状逐字节）；env off → 零重试；backoff 期间 abort → INTERRUPTED 不进二轮。

### 4.6 Mini-ADR

- **HX-C1 瞬态集 = 类型注册表，初始仅 `AllProvidersExhaustedError`**：类型判定零文本嗅探（router 已在 4xx/5xx 分流处做过判定，HX-3 不重复造分类器）；未知异常默认永久。扩展 = 注册表加一行。
- **HX-C2 replay-safety 守卫 = checkpoint 尾部判定**：committed 历史不重放 → 全 run irreversible 标记既过保守（committed 的 irreversible 无重放风险）又非必要；唯一风险窗口 = 尾部 dangling 批次，CM-B5 capability-bounded 规则上移复用。守卫路径 fail-closed（与 fail-open 公理不冲突：公理上限是"多花 token"，重复副作用越界）。
- **HX-C3 in-worker 同 run_id 重试**：非新 run——SSE 流连续、trajectory 单记录、RunManager/审计零改动；`retry` 事件进 event log 保历史真相。与 J-24 resume（新 run_id）语义区分：resume 是用户动作产生的新执行段，retry 是同一执行段的故障恢复。
- **HX-C4 取证修正入档**：评估 ⑧ 的两个具体案例修正——沙盒 acquire 失败已被 E-12 在工具层自愈（非 run 级故障）；"run 零 irreversible 调用"守卫精确化为尾部 dangling 判定。

### 4.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §4 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1（收尾） | 瞬态注册表 + 尾部守卫 + run_agent 重试循环 + retry 事件 + counter + env 配置 + 测试 | §4.5；全链回归；零债 6 条 |

---

## 5. HX-4 — 可观测补强

### 5.1 现状取证（2026-06-11，main@147a313）

| 评估断言 | 实况 | 证据 | 判定 |
|---------|------|------|:----:|
| "工具延迟直方图缺" | `helix_tool_latency_seconds{tool}`（桶 0.01-60s）+ `helix_tool_call_total{tool,outcome}` 已在，TOOL_CALL audit 还带 `duration_ms` | `builder.py:142-153,1087-1098,1139` | **取证修正 ①：已在** |
| "run 成功率 counter 缺" | `helix_session_duration_seconds{outcome}` 五种 outcome（success/error/max_steps/interrupted/cancelled）每终态恰好一次 observe——histogram `_count` 即按 outcome 的 run 计数，成功率 PromQL 直接推导 | `sse.py:116-121,577` | **取证修正 ②：可推导，不加冗余 counter**（缺的是 recording rule） |
| "approval 队列 gauge 缺" | timeout sweep **已在** retention-cleanup-job（J.8-step3b：`list_expired`→`mark_decided(TIMEOUT)`），但 pending 数无 gauge；retention job 是短命 cron，gauge 必须住常驻进程 | `retention_cleanup_job/job.py:153-181`、`approval/base.py:45-51` | 真 gap ①（gauge），sweep 不缺 |
| "checkpoint 持久化延迟缺" | checkpointer = langgraph 原生 saver（dev InMemory / prod AsyncPostgresSaver），factory 裸返回，零计时 | `checkpointer/factory.py:44-83`、`runner.py:58-60` | 真 gap ② |
| "结构化日志 run_id/trace_id 贯穿缺" | JSON formatter + trace_id **已完整贯穿**（W3C header→OTel→contextvar→formatter→feedback 表）；run_id 只有 `%s` 字符串拼接，无 contextvar、JSON 无独立字段 | `log.py:100-168`、`middleware/observability.py:76-106`、`common/context.py:26-95` | 半过期：只缺 run_id 轴 |
| —（取证新发现） | `docs/runbooks/slo.md` 7 条 SLO + `tools/observability/rules/sli.yml` recording rule 框架在；HX-1 drift / HX-3 retry / 本节新指标均未进 rules 与 SLO 文档 | `slo.md:9-49` | 真 gap ③（资产同步） |

gauge helper 已在（`helix_gauge`，`metrics.py:121-132`）且有先例：skill_curator 周期 worker 每 cycle `set_curator_pinned_skills(n)`（`skill_curator.py:195`）。

### 5.2 设计

**① approval pending gauge（gap ①）**

- `ApprovalStore.count_pending() -> int` 抽象 + 双实现（SQL：`COUNT(*) WHERE status='pending'`）。`agent_approval` 表跨租户计数的 RLS 形态实施期核实：tenant-scoped 表则按 ledger 先例处理（FORCE-RLS → SET ROLE / ENABLE-only → owner 豁免），单 gauge 不分租户标签（防 label 基数爆炸；per-tenant 数从 audit/API 查）。
- control-plane lifespan 内轻量周期任务（curator 同款骨架，interval 60s，复用现有 worker 节奏不新开配置面）：`helix_control_plane_approvals_pending` gauge `.set(n)`。读失败 log + 跳过本 cycle（fail-open：可观测故障绝不影响业务路径）。

**② checkpoint 计时 wrapper（gap ②）**

- `helix-runtime/checkpointer/timing.py`：`TimingCheckpointSaver` 代理 `BaseCheckpointSaver` 四个 IO 方法（`aput`/`aput_writes`/`aget_tuple`/`alist`），`helix_checkpoint_op_seconds{op}` histogram（桶对齐 0.005-10s 量级；`alist` 流式则计首批返回时延或不计——实施期按基类签名定）。计时层 never-fail：观测代码异常吞掉照常透传调用（fail-open）。
- `make_checkpointer` 统一包两种实现（backend 不进 label——dev InMemory 数据没人看，统一包保测试路径同形）。`GraphRunner` 零改动。

**③ run_id 结构化贯穿（gap ③ 半轴）**

- `common/context.py` 增 `current_run_id` contextvar（trace_id 同款三函数面）；`HelixJsonFormatter` 输出增 `run_id` 字段（未设 = null，与 trace_id 同语义）。
- 绑定点 = `run_agent` 入口 set / `finally` reset（worker task 是 run 的执行边界，三个 spawn 点零改动）；既有 `run_id=%s` 消息文本不动（surgical——消息可读性照旧，结构字段管检索）。
- trace_id 已贯穿，零改动。

**④ recording rules + SLO 文档同步（gap ③）**

- `tools/observability/rules/sli.yml` 增：run 成功率（`sum(rate(helix_session_duration_seconds_count{outcome="success"})) / sum(rate(..._count))`）、HX-3 retry 恢复率、HX-1 token 估算漂移比、approvals pending、checkpoint op P95。
- `docs/runbooks/slo.md` 增对应 SLI 行（目标值留 TBD——基线数据未到不拍数字，公理：不编造验收线）。

### 5.3 边界（不做）

- Grafana dashboard JSON 不在本条（provisioning 目录全仓为空，是独立 infra 工作项；recording rules 已让数据可查可告警）。
- retention job 的 `approvals_timed_out` 不发 Prometheus 指标（短命 cron 与 pull 模型不合，audit + 日志已记录；上 pushgateway 是过度工程）。
- 不做 per-tenant 指标标签（基数纪律）。
- 不动既有任何指标命名/标签（零迁移成本）。
- OTel span 覆盖面扩展（tool span / checkpoint span）不在本条——trace 体系另议，本条只补 metrics + 日志轴。

### 5.4 测试

- `count_pending` 双实现 + gauge 周期任务（fake store 计数 → gauge 值断言；store 抛错 → cycle 不死）。
- `TimingCheckpointSaver`：包 fake saver，四方法透传语义不变 + histogram 样本落账 + 观测层异常不影响透传（fail-open 断言）。
- run_id contextvar：formatter 输出含/不含 run_id 两态；run_agent 运行期内 contextvar 已设、终态后已清。
- sli.yml 语法校验（promtool 不在 CI 则 YAML parse 冒烟）。

### 5.5 Mini-ADR

- **HX-D1 不加冗余 run 成功率 counter**：histogram `_count` 已是按 outcome 的精确计数，加 counter 违反单一真相源；缺口在查询层（recording rule），不在 emit 层。
- **HX-D2 approval gauge 住常驻进程、周期 set**：retention cron 短命与 pull 模型不合；curator gauge 先例同款。单 gauge 不分租户。
- **HX-D3 checkpoint 计时 = 代理 wrapper**：langgraph saver 无观测缝，factory 处包一层是唯一不侵入点；观测 never-fail。
- **HX-D4 run_id 走 contextvar + formatter 字段**：与 trace_id 同构；绑定在 run_agent 任务边界，spawn 点零接触。
- **HX-D5 取证修正入档**：评估 ⑨ 五项里两项过期（工具延迟已在、成功率可推导）、一项半过期（trace_id 已贯穿）；真缺口收敛为 gauge + checkpoint 计时 + run_id 轴 + 查询层资产。

### 5.6 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §5 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1（收尾） | count_pending + gauge 任务 + TimingCheckpointSaver + run_id contextvar/formatter + sli.yml/slo.md 同步 + 测试 | §5.4；全链回归；零债 6 条 |
