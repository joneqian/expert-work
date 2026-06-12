# Stream HX — Harness 强化（设计先行）

> **背景**：2026-06-10 完成 helix harness 十维能力评估（`docs/research/2026-06-10-helix-harness-capability-assessment.md`），用户拍板把全部非强项做强，按 Wave 严格顺序推进（Wave 1 零债收完再进 Wave 2）。每条子项设计先行 + 零债收尾（Stream CM 同节奏）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条子项 PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **横切公理**（HX-12/13 立项时锁定，对全 Stream 生效）：① 不存在 drop core tool / drop 历史真相的代码路径（视图级裁剪 ≠ 状态删除）；② fail-open——基础设施故障的代价只能是多花 token，绝不能是少能力；③ config 防御解析（clamp / safe default，不 raise）。
>
> **本文件状态**：Wave 1（HX-1~4，§2-§5）已全部交付。Wave 2（2026-06-11 起）：HX-5（§6）已交付；HX-6（sandbox 热池 + 资源限额粒度，§7）详设已锁定；其余各条开工时追加章节。

---

## 1. 范围 & 边界

| ID | 评估维度 | Gap | 交付 | 详设 |
|----|---------|-----|------|------|
| **HX-1** | ③ 上下文工程 | `len//4` 估算漂移（CJK 严重低估）+ `context_window` 不随目录解析 + E.3 遗留 8K 默认裁剪 | TokenEstimator 协议 + tiktoken 默认实现 + 目录解析 + 遗留默认值退役 | §2（本文） |
| HX-2 | ⑦ 学习闭环 | 👎 无学习消费者（断点精确化见 §3.1） | rollback gate 接 👎 + 记忆 review 标记 + feedback consumer worker | §3（本文） |
| HX-3 | ⑧ 容错 | run 级瞬态故障无自动重试 | 瞬态分类 ∧ replay-safe 守卫 → 重试 1 次 | §4（本文） |
| HX-4 | ⑨ 可观测 | approval 队列 gauge / checkpoint 延迟 / run_id 结构化贯穿缺（工具延迟与 run 成功率判定过期，见 §5.1） | gauge + checkpoint 计时 wrapper + run_id contextvar + recording rules 同步 | §5（本文） |
| HX-5 | ① prompt 工程 | manifest 覆盖式更新无历史/diff/回滚；无离线 variant 对比 | `agent_spec_revision` 不可变历史 + 回滚 + diff API/UI + 离线 A/B harness | §6（本文） |
| HX-6 | ⑤ 沙盒 | 首触冷启动（J.15 暖会话已在）+ 池路径限额配对 | READY 池 + replenisher + claim 时 docker update + 镜像预拉 | §7（本文） |
| HX-7 | ⑨ 可观测 / 治理 | Langfuse 停在 Recording stub（OTel 链已通，见 §8.1）；approval 无队列视图/批量 | LangfuseSdkClient + settings/factory 接线 + approval list API + 批量 decide + /approvals 队列页 | §8（本文） |
| HX-8 | ⑩ 多租户 | 平台 provider/tool 凭证全租户共享一把上游 key（爆炸半径/成本归因/限流隔离全缺）；跨租户查询零开关（仅 audit） | per-tenant 凭证 override（平台管理，非 BYOK）+ 部署级跨租户 block 开关 | §9（本文） |
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

---

## 6. HX-5 — prompt 版本管理 + 离线 A/B

### 6.1 现状取证（2026-06-11，main@c509722）

| 事实 | 证据 | 判定 |
|------|------|:----:|
| 最终 system prompt = 多源实时组合：manifest `system_prompt.template` + skill fragments（prompt_fragment/behavior_patches/tool_notes）+ 动态注入（reminders/记忆/日期） | `agent_spec.py:746`、`agent_factory._assemble_system_prompt:925-996` | "prompt"的唯一可控源 = manifest |
| `agent_spec` 按 `(tenant, name, version)` 存储，`update_spec` **原地覆盖** `spec_json`，零历史；version 是用户起的文本标签非递增序 | `agent_spec/sql.py:129-162` | 真 gap ①：误编辑不可回滚、不可 diff |
| 行内注释已有既定意图："M1 introduces a row history table" | `agent_spec/sql.py:141` | HX-5 = 兑现该意图 |
| audit 仅记 `MANIFEST_WRITE` + 新 `spec_sha256`，无 old/new 对照，update 路径不读旧值 | `api/agents.py:428-436` | 真 gap ②：变更不可审计回放 |
| skill 版本系统是成熟先例：整数递增、行不可变 append-only、promote/rollback、eval 回归门 | `models/skill.py:146-230`、SE-7 | 抄此先例 |
| `helix_eval.run_eval(eval_set, complete)` 轻量 harness 在：YAML EvalSet（cases+assertions）→ per-case 判定 → EvalReport；judge 是断言式非 LLM | `tools/eval/helix_eval.py:145-157`、`datasets/example.yaml` | 离线对比的三件套有两件（任务集+运行器），缺 variant 对比层 |
| run 创建在 `graph_input["messages"][0] = SystemMessage(built.system_prompt)` 单点固定，`configurable` 可扩展 | `api/runs.py:371-401` | online A/B 接缝在但不做（HX-11） |
| admin UI 有 Monaco YAML 编辑器（ManifestEditor），无版本历史/diff/回滚界面 | `AgentDetail.tsx`/`ManifestEditor.tsx` | UI 面要补 |

**范围界定**：评估 ① 说"prompt 无版本管理"。prompt 是多源组合，但 skill fragments 已有自己的版本系统（SE-7），动态注入是运行期行为——唯一无版本的可控源就是 manifest。所以 HX-5 的版本实体 = **manifest 整体修订史**（system_prompt 是其中最高频变更字段），而非单拆一个 prompt 字段表：单字段表覆盖不了"改了 policies 想回滚"的同型需求，且与 sql.py:141 的既定意图一致。

### 6.2 设计

**① `agent_spec_revision` 不可变修订史（PR1）**

- 新表：`agent_spec_revision(id, tenant_id, agent_name, agent_version, revision INT 递增, spec_json JSONB, spec_sha256, actor_id, created_at)`；唯一约束 `(tenant_id, agent_name, agent_version, revision)`；RLS 与 agent_spec 同形（tenant-scoped ENABLE）。
- `create_spec` 写 revision 1；`update_spec` 读旧行→append revision N+1→覆盖主行（事务内）；行永不 UPDATE/DELETE（历史真相公理）。sha 未变的 no-op update 不产生新 revision。
- audit `MANIFEST_WRITE` details 增 `revision` + `prev_sha256`（固定字段，不 log 请求派生值）。
- store 抽象 + 双实现：`list_revisions(tenant, name, version, limit/offset)`、`get_revision(..., revision)`。

**② 回滚 + diff API + admin UI（PR2）**

- `POST /v1/agents/{name}/{version}/revisions/{n}/rollback`：取 revision n 的 spec_json → 走既有 update_spec 校验管线 → 产生新 revision N+1（回滚=前进到旧内容，不删历史；skill 先例同语义）。审计 `MANIFEST_WRITE` + details `rolled_back_to`。
- `GET .../revisions` / `GET .../revisions/{n}`：返回快照；**diff 不在服务端算**——返回两份快照由 UI Monaco diff 渲染（存 diff/算 diff 都是可派生冗余）。
- admin UI（SE-8 接线点清单全过）：AgentDetail 增 History tab——revision 列表（actor/时间/sha 短码）+ 任意两版 Monaco diff view + 单版回滚按钮（确认对话框）；i18n 双语；Playwright + Storybook。Monaco diff 组件用 `@monaco-editor/react` DiffEditor（testid 包 wrapper div——[memory:monaco-data-testid]）。

**③ 离线 variant 对比 harness（PR3，收尾）**

- `tools/eval/prompt_ab.py` CLI：输入 = agent spec 文件（或 name+revision 两个引用）×2 + EvalSet YAML + provider 配置；对每个 variant 用其 system_prompt 组装 CompletionFn（复用 helix_eval 既有 provider 适配），跑同一 eval set，输出对比报告（per-case A/B passed 矩阵 + 通过率 Δ + McNemar 不对称计数——n 小用精确二项，不编 p 值阈值，报数让人判断）。
- 真 LLM 路径**不进 CI**（[memory:ci-no-model-keys]）：CI 只测 harness 本身（fake CompletionFn 双 variant 全链）。
- 输出落 `eval-out/`（与 run_longmem 同款 artifact 形态），不建库表——离线对比是工具不是平台状态；结果要留档时由用户 commit baselines（CM-N5 同模式）。

### 6.3 边界（不做）

- online A/B（流量分桶、variant 路由）= HX-11（Wave 3，依赖本条）；本条只留接缝事实（§6.1 末行），不动 runs.py。
- 不做 LLM judge（helix_eval 是断言式；M1 J.13a-2 既定项，另立）。
- 不做 revision 自动裁剪/保留策略（manifest 编辑频率低，JSONB 快照成本可忽略；爆炸了再立项）。
- 不版本化 skill fragments / 动态注入（各有体系）。
- 不做 revision 间自动语义分析（"这次改动影响了什么"）——diff 视图足够。

### 6.4 可观测

- audit 已覆盖（MANIFEST_WRITE 增强字段）；revision 写失败 = update_spec 事务失败，既有错误路径。
- 不加新指标（manifest 编辑是低频管理操作，audit 即可观测）。

### 6.5 测试

- **PR1**：双 store revision 生命周期（create→1 / update→2,3 / no-op 不增 / 唯一约束）；事务性（revision append 与主行覆盖同生死，真 PG 集成测）；audit 字段断言；RLS 隔离（跨租户 list_revisions 零行）。
- **PR2**：rollback 端点（产生新 revision/校验管线拒坏 spec/404 矩阵）；revisions API envelope 对账（[memory:envelope-vs-raw]）；UI——History tab 渲染/diff 视图/回滚流（vitest + Playwright）。
- **PR3**：harness fake-provider 全链（两 variant 出对比报告）；报告格式快照测试；CLI 参数防御解析。

### 6.6 Mini-ADR

- **HX-E1 版本实体 = manifest 修订史**：prompt 是多源组合，唯一无版本的可控源是 manifest；单拆 prompt 字段表覆盖不了同型需求（policies/model 误编辑），且兑现 sql.py:141 既定意图。
- **HX-E2 revision 不可变 + 回滚=前进**：append-only，回滚产生新 revision 而非删除——skill 先例 + 历史真相公理。
- **HX-E3 diff 读取侧渲染**：服务端只存/返快照，diff 是派生数据不落库不算两遍。
- **HX-E4 离线 A/B = 工具不是平台状态**：harness 进 tools/eval，artifact 落 eval-out；真 LLM 手动跑，CI 只验 harness 本身（fake provider）。
- **HX-E5 报数不报结论**：对比报告给通过率 Δ + 不对称计数，不内置显著性阈值拍"谁赢"——150 case 量级的离线对比由人判断。

### 6.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §6 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1 | `agent_spec_revision` 迁移 + 双 store + create/update 接线 + audit 增强 | §6.5-PR1；全链回归 |
| PR2 | rollback/revisions API + admin UI History tab（diff + 回滚） | §6.5-PR2；前端全链 |
| PR3（收尾） | `prompt_ab.py` 离线对比 harness + 文档 | §6.5-PR3；零债 6 条 |

---

## 7. HX-6 — sandbox 热池 + 资源限额粒度

### 7.1 现状取证（2026-06-11，main@2894952）

| 评估断言 | 实况 | 证据 | 判定 |
|---------|------|------|:----:|
| "每 acquire 冷启动" | **J.15 per-user 暖会话已在**：`(tenant, user)` → sandbox 复用，release = keep-alive no-op，15min idle reaper；同用户后续 acquire 是暖路径（`cold_start=false`） | `supervisor.py:123-126,408-450`、`reaper.py` | 半过期：冷的只剩**首触**（用户第一次 / variant 切换 / TTL 后） |
| "per-call CPU/mem cgroup 限额缺" | cpu/memory_mb/pids_limit **已是 per-acquire 字段**，经 `docker run --cpus/--memory/--pids-limit` 容器级生效；exec 级无重设 | `schemas.py:31-35`、`runtime_provider.py:61-106` | 半过期：acquire 粒度已在；真 gap = 池路径的限额配对（§7.2-③）；per-exec 动态限额不做（§7.3） |
| —（架构事实） | M0 Mini-ADR F-4 显式不做 pool；`SandboxState` 无 READY；`sandbox_instance` 表注释"为 M1 warm pool 铺路"；SLO #4 "<500ms (M1 warm pool)" | `STREAM-F-DESIGN §1.2`、`domain.py:16-26`、`slo.md:16` | HX-6 = 兑现 M1-A 既定项 |
| —（取证新发现，**关键约束**） | persistent workspace = per-user named volume，`docker run -v` **创建时挂载**；预启动的池容器无法事后挂用户卷 | `supervisor.py:158-171`、docker 挂载语义 | **持久工作区用户的首触 acquire 在 docker 范式下不可池化** |
| —（容量事实） | per-tenant 配额 50（CREATING+IN_USE 计数，429 拒绝）；supervisor 无全局并发上限 | `settings.py:54-59`、`supervisor.py:460-474` | 池容器不占租户配额（中性资源，未绑租户） |

**范围界定（关键 trade-off，过堂点）**：暖路径矩阵——

| acquire 形态 | 现状 | HX-6 后 |
|--------------|------|---------|
| 同用户复购（J.15 会话活着） | 暖 ✅ | 暖（不动） |
| 临时沙盒（无 user_id：trigger 一次性 / eval / playground 匿名） | 冷 | **池命中 → <500ms** |
| 持久用户首触 / TTL 后 / variant 切换 | 冷 | 仍冷（镜像预拉砍掉拉镜像尾巴）；数据面方案（卷内容迁移 / microVM snapshot）= M2 另立项 |

业界对照：E2B/Modal 的全形态 warm 靠 microVM snapshot/restore，docker 范式池化普遍只覆盖 stateless 沙盒。诚实交付：池覆盖临时沙盒 + 镜像预拉保底全形态，**不把"首触仍冷"包装成已解决**（[memory:no-design-choice-disguise]——这里是真技术约束非能力降级，证据在案）。

### 7.2 设计

**① READY 池 + replenisher（PR1 核心）**

- `SandboxState` 增 `READY`（池中预启动，未绑租户/用户）；`SandboxRecord.tenant_id` 池容器期间用平台哨兵值（实施期定：nullable 迁移 vs 哨兵 UUID，倾向哨兵零 schema 变更）。
- 池 per image variant：`pool_size_minimal` / `pool_size_office` settings（默认 minimal=2 / office=0，**0=关**——dev/CI 不预启动；config 防御解析 clamp [0, 16]）。
- `PoolReplenisher` 后台任务（reaper 同款骨架，interval 复用 reaper 节奏）：count(READY per variant) < target → `docker run`（tmpfs workspace、默认限额）补齐；超额（settings 调小后）销毁多余。补齐失败 log + counter，下轮重试（fail-open：池故障 = 退回冷启动，绝不影响 acquire 正确性）。
- acquire 路径插在 J.15 会话查找之后：无 user_id（或 user 无持久卷需求）∧ 池有 READY → claim（状态 READY→IN_USE，绑定 tenant，CAS 防双取）→ **`docker update --cpus --memory --pids-limit`** 套请求限额 → 返回 `cold_start=false`。claim 失败/池空 → 现状冷启动路径，行为逐字节不变。

**② 限额配对（PR1，"per-call 限额"的真形态）**

- 池容器预启动用默认限额；claim 时 `docker update` 到请求值（CliDockerClient 增 `update_limits`；`--pids-limit` docker update 支持，实施期核 CLI 版本面）。update 失败 → 销毁该容器走冷启动（fail-closed：限额是安全面，宁可慢不可错）。
- per-exec 动态限额**不做**（§7.3）。

**③ 镜像预拉（PR2）**

- supervisor lifespan 启动时对两个 variant 镜像 `docker image inspect`，缺失则 `docker pull`（后台 task，不阻塞 ready；失败 warn + counter，acquire 时 docker run 自己会拉——fail-open）。覆盖"节点重建后首个冷启动拖 30s+ 拉镜像"的尾巴，全 acquire 形态受益。

**④ 可观测（PR2）**

- `helix_sandbox_pool_ready` gauge（per variant）+ `helix_sandbox_pool_total{event=hit/miss/replenish/replenish_failed/claim_raced}` counter。
- `helix_sandbox_cold_start_seconds` 语义不变（池命中不计入，同 J.15 暖路径）；SLO #4 M1 行的验收数字 = 池命中率 + 命中路径延迟（claim+update 应 <500ms）。

### 7.3 边界（不做）

- **per-exec 动态限额**：manifest 资源声明是 agent 级，单沙盒内逐 exec 变限额无业务方需求；每 exec 前后 `docker update` 往返加延迟。acquire 粒度（已在）+ 池 claim 配对（本条）覆盖真实需求面。需求出现再议。
- **持久工作区首触池化**：docker 挂载约束（§7.1）；卷数据迁移 / CRIU / microVM = M2 量级另立项。
- 镜像 layer cache / registry push（M1-A 另一半）、EWMA 自适应池伸缩（先固定 size 拿数据）、ulimit/disk quota 扩展（独立安全面）。
- 池容器不计租户配额；claim 时才进 `count_active_for_tenant`（配额语义不变）。

### 7.4 测试

- **PR1**：池生命周期（replenish 到 target / settings=0 不起 / 调小销毁多余）；claim 矩阵（命中→update+绑定+IN_USE / 池空→冷启动 / update 失败→销毁+冷启动 / 双并发 claim 一胜一冷 / 有 user_id 持久卷请求绕过池）；J.15 会话优先于池；配额计数不含 READY。全部 RecordingDockerClient/InMemoryStore 单测 + 1 条真 docker integration（池起→claim→exec→release）。
- **PR2**：预拉（缺镜像触发 pull / 已在跳过 / 失败不阻塞 lifespan）；指标断言。

### 7.5 Mini-ADR

- **HX-F1 池 = per-variant READY 容器 + 固定 size replenisher**：EWMA 伸缩留到有命中率数据后；0=关，fail-open（池故障退冷启动）。
- **HX-F2 池只服务无持久卷 acquire**：docker named volume 创建时挂载是硬约束；持久用户暖靠 J.15（已在），首触冷留 M2 数据面方案。诚实边界，不包装。
- **HX-F3 claim 时 docker update 配对限额**："per-call 限额"的可行真形态；update 失败 fail-closed（销毁走冷启动）——限额是安全面。
- **HX-F4 镜像预拉 = 全形态保底**：唯一对持久用户首触也有效的加速件；后台 best-effort。
- **HX-F5 取证修正入档**：评估 ⑤ 两断言各半过期（J.15 暖会话已在 / 限额已 per-acquire）；真缺口 = 首触池化（受限做）+ 池路径限额配对 + 镜像预拉。

### 7.6 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §7 + ITERATION-PLAN tick | 纯 docs，CI |
| PR1 | READY 状态 + 池 + replenisher + claim/update 限额 + settings | §7.4-PR1；全链回归 |
| PR2（收尾） | 镜像预拉 + 指标/SLO 同步 + 文档 | §7.4-PR2；零债 6 条 |

---

## 8. HX-7 — trace 生产接线 + approval 队列页

### 8.1 现状取证（2026-06-12，main@8dcdf06）

| 评估断言 | 实况 | 证据 | 判定 |
|---------|------|------|:----:|
| "trace 无生产后端" | **OTel 链已全通**：W3C traceparent 提取 → contextvar → `X-Helix-Trace-Id` 回送 + 日志贯穿（HX-4 又加 run_id 轴）；`otlp_traces_endpoint` settings 已在 | `middleware/observability.py:84-106`、`context.py:30-36`、`settings.py:66` | 半过期：基础设施 trace（Tempo 路线）只差部署；**真缺口 = Langfuse 档** |
| "Langfuse 接线缺" | Protocol（`LangfuseClient`/`LangfuseSpan`）+ `RecordingLangfuseClient` stub + middleware 全在且 fail-soft 全包；**SDK adapter 不存在**（E.5 自注 "follow-up PR"——本子项兑现既定意图）；`langfuse` 依赖未加；settings 无 langfuse 项；`runtime.py:43` 硬编码 Recording | `middleware/langfuse.py:10-17,116-122`、`runtime.py:43`、全仓 pyproject grep | 准确 |
| "approval 无队列视图" | 模型/store/单 run API（GET run + POST resume）/RunDetail 内嵌 ApprovalCard 全在；**缺独立 list API、队列页、批量操作**；Badge 轮询 `/v1/runs?status=paused` 间接计数 | `runs.py:448-510,607-793`、`ApprovalCard.tsx`、`ApprovalPendingBadge.tsx:28-92`、router.tsx 无 /approvals | 准确 |
| —（取证修正） | approval **24h 超时自动 TIMEOUT 已在**：retention-cleanup-job 每轮扫 `list_expired` → `mark_decided(TIMEOUT, decided_by="system")` | `retention_cleanup_job/job.py:169-180` | 子代理初判"缺超时 worker"有误，已核实在案 |
| —（架构事实） | resume 的 continuation worker 是 detached task，SSE 流只是消费端（断开不影响 run 完成）；`mark_decided` 自带 CAS（lost race → False → 409） | `runs.py:756-773,670-673` | **批量 decide 可做非流式端点**——worker/流解耦是现成前提 |

### 8.2 设计

**① Langfuse SDK adapter（HX-7a，PR1）**

- `LangfuseSdkClient` 实现既有 `LangfuseClient` Protocol，新模块 `packages/helix-runtime/src/helix_agent/runtime/middleware/langfuse_sdk.py`；middleware 一行不动（缝就是 Protocol——Mini-ADR HX-G1）。
- 依赖：`langfuse>=3,<4` 进 helix-runtime（v3 SDK 基于 OTel，与现有 `opentelemetry-sdk>=1.27` 同源——Langfuse span 自动挂进活跃 OTel trace，ADR-0005 "trace_id 共享、Langfuse↔Tempo 互跳" 数据流**零额外代码兑现**）。import 失败防御降级 Recording（损坏安装不杀服务）。
- 语义映射（实施期以 SDK 文档核准确切方法名，不臆测）：`start_span(name,input,metadata)` → SDK generation（LLM 语义，token/cost 统计入账）；`record_output` → update(output)；`record_usage` → update(usage_details)；`record_error` → update(level=ERROR,status_message)；`end` → end()。
- flush 语义：SDK 内部 bounded queue + 后台线程（Protocol 注释的预设形态）；control-plane lifespan teardown 调 `flush()`/`shutdown()` 有界等待。

**② settings + runtime 接线（PR1）**

- control-plane settings 增 `langfuse_host` / `langfuse_public_key` / `langfuse_secret_key`（env `HELIX_CONTROL_PLANE_LANGFUSE_*`；secret 仅 env 注入，不进代码/compose 明文——object_store keys 先例）。
- `runtime.py` 改 factory：三项齐全 → `LangfuseSdkClient`；缺任一 → `RecordingLangfuseClient` + info log。**配置缺省 = Recording 是合法生产形态**（fail-open；CI/dev 无凭证纪律——真实例验证走手动/SE-9，CI 用 fake 测 adapter 映射）。
- 不起 dev compose Langfuse 实例：v3 自托管栈重（ClickHouse+PG+Redis+MinIO），接线指向外部实例，部署属 infra 项（§8.3）。

**③ approval list API（HX-7b，PR2）**

- store：`ApprovalStore.list_by_status(status, limit, offset) -> tuple[list[ApprovalRecord], int]`（含 total；SQL `ORDER BY requested_at ASC`——队列语义最老优先）。RLS tenant-scoped 自然隔离；system_admin 跨租户沿用 Stream N `tenant_id=⋆` 既有模式。
- API：`GET /v1/approvals?status=pending&limit&offset`（status 默认 pending，支持全部终态查历史）→ `{items, total}`。
- 列表项 = ApprovalRecord 自足字段（action_summary/reason_kind/requested_at/timeout_at/run_id/thread_id/user_id），**不 join agent_name**：详情语境跳 RunDetail 已有全上下文，列表 join 跨表换一列展示不值（业务价值/工作量都不立）。

**④ 批量 decide（PR2）**

- 从 resume 端点提取共享内核 `_apply_decision(...)`（verdict CAS → audit → aupdate_state → spawn detached worker），resume 端点改调内核 + 保留 SSE 返回（语义零变）。
- 新端点 `POST /v1/approvals:decide`：body `{decisions: [{thread_id, run_id, decision, modified_args?, reason?}]}`，**上限 20/批**（每项 approve 即 spawn 一个 LLM continuation run——资源面要有界）；非流式 JSON 返回 per-item `{run_id, ok, error?}`。
- 部分失败语义：逐项独立——单项 409（already-decided/timeout 赛跑）/404 不中断其余；worker 与 SSE 已解耦故批量 approve 的 N 个 continuation 自然后台跑，操作员在 RunDetail 看各自进度。

**⑤ admin UI 队列页（PR3，收尾）**

- `/approvals` 路由 + Sidebar 独立 "Approvals" 导航项（`ApprovalPendingBadge` 从 Runs 迁挂此项，数据源改 `GET /v1/approvals?status=pending&limit=1` 的 total——比 `runs?status=paused` 语义更准）+ CommandPalette + SDK（`listApprovals`/`decideApprovals`）+ i18n 双语 + Storybook + Playwright（SE-8 接线点清单全量）。
- 列表（requested_at 最老优先）：reason_kind Tag / action_summary / 等待时长 / timeout 倒计时 / run 链接（跳 RunDetail 看完整上下文 + proposed_args 编辑）；行内 approve/reject + 多选批量——**单条与批量统一走 `:decide` 端点**（size=1），UI 不维护两套决策路径；带 modify 的精修仍引导去 RunDetail ApprovalCard（队列页不重复 JSON 编辑器）。

### 8.3 边界（不做）

- **Langfuse 实例部署**（compose/Helm/运维 runbook）：infra 项另立；本子项交付接线能力 + Recording 缺省。
- trace 查看 UI（嵌入/外链）：STREAM-H §H.3 已有设计归属，不在此扩展。
- 审批通知（webhook/邮件/IM 提醒审批人）：通知通道是独立能力面（M2 评估）。
- approval 队列的 system_admin 专属跨租户聚合 UI：TenantScope 既有机制已覆盖切换查看。

### 8.4 测试

- **PR1**：adapter 单测（fake SDK 对象注入——五方法映射 / usage 字典透传 / error 字符串化 / flush 委托 / import 失败降级 Recording）；factory 三态（齐全→SDK / 缺项→Recording / import 错→Recording）；middleware 既有测试零改动即回归。
- **PR2**：list API（status 过滤 / 分页 + total / 默认 pending / RLS 隔离 / 空窗）；批量（混合 verdict / 单项 409 不中断 / 上限 21 → 422 / 空列表 422 / 复用内核后单 resume 全量回归）。
- **PR3**：队列页组件测（列表渲染 / 多选批量调 SDK / Badge 新数据源）+ Storybook + Playwright 冒烟。

### 8.5 Mini-ADR

- **HX-G1 adapter 实现既有 Protocol，middleware 零改动**：E.5 把缝留在 `LangfuseClient`，本子项只是把 stub 换成可配置的真实现——架构无新决策面。
- **HX-G2 `langfuse` 依赖进 helix-runtime + import 防御降级**：observability 是平台默认能力，不做 extra 可选安装位；损坏的 SDK 安装降级 Recording 而非启动失败。
- **HX-G3 配置缺省 = Recording（fail-open）**：无凭证的部署/CI/dev 保持现行为字节不变；trace 故障域永不进 LLM 主路径（middleware fail-soft 已兜）。
- **HX-G4 批量 decide = 共享内核 + 非流式端点**：worker/SSE 解耦是现成架构事实；上限 20 防 continuation 风暴；逐项独立失败。
- **HX-G5 队列页单/批统一走 `:decide`，modify 留在 RunDetail**：UI 一套决策路径；JSON 精修是单 run 语境操作，队列页不复制编辑器。

### 8.6 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §8 + ITERATION-PLAN 登记 | 纯 docs，CI |
| PR1 | LangfuseSdkClient + 依赖 + settings + runtime factory + teardown flush | §8.4-PR1 |
| PR2 | list_by_status store/API + `:decide` 批量端点（resume 内核提取） | §8.4-PR2；resume 全量回归 |
| PR3（收尾） | /approvals 队列页 + Badge 迁移 + SE-8 全接线 | §8.4-PR3；零债 6 条 |

---

## 9. HX-8 — 多租户薄点：per-tenant 凭证 override + 跨租户 block 开关

> 2026-06-12 拍板（用户确认）：HX-8a 形态 = **per-tenant override（平台管理，非 BYOK）**；HX-8b 层级 = **部署级 settings 开关**。per-tenant opt-out 与租户自助 key 均不在范围。

### 9.1 现状取证（2026-06-12，main@0312e0d）

1. **平台凭证管理面已完整存在**（Stream P/Q），HX-8a 不是从零建凭证面，是给既有 overlay 加租户维度：
   - 存储：`platform_provider_secret` / `platform_tool_secret`（迁移 0049，PK 单列 `provider`/`tool`，**无 tenant_id 列、无 RLS**，tenant-less 平台全局）。
   - 视图：`PlatformSecretsService`（`control_plane/platform_secrets.py`）——env seed + DB overlay 合并，**DB wins / disabled 行 suppress**（P-12），TTL 30s 缓存 + 写端点 `invalidate()`。
   - API：`/v1/platform/credentials` system_admin CRUD（`api/platform_config.py`）——`value` 写穿 SecretStore 生成 `secret://` ref 或运营直给 `secret_ref`（二选一互斥校验），**真值永不进 catalog/audit**（Q-4/Q-7）。
   - UI：`SettingsPlatformConfig.tsx`。
2. **resolver 的租户接缝已在签名里**：`CredentialsResolver.resolve_provider/resolve_tool`（`helix-common/credentials/resolver.py:101`）已收 `tenant_id` 参数，但现仅做租户存在性验证（Y-1）——凭证查询 100% 平台级。embedder/reranker/web_search/agent 主模型 key 全走此路径（`control_plane/runtime.py:455-618`）。
3. **Y-1 边界不动摇**：BYOK 已移除（`CredentialsMode = Literal["platform"]`，0058 冻结 tenant_config 凭证字段，`SettingsTenantCredentials` 削成只读）。HX-8a 的 override key 仍是**平台采购、平台管理、system_admin 配置**，租户不可见不可自配——爆炸半径隔离 + 上游成本归因（Stream Y 计量衔接），非 BYOK 回潮。
4. **rate_card（0059）示范了 NULL-tenant 单表双态模式**，但其前提是建表时就预留了 nullable `tenant_id`；`platform_*_secret` 两表 PK 是单列文本、无预留——改 PK 属破坏式迁移（见 Mini-ADR HX-H1）。
5. **跨租户查询单一决策点**：`ensure_tenant_scope`（`control_plane/tenant_scope.py:73`）覆盖全部 14 个 list 端点（含 HX-7 新加的 `GET /v1/approvals`）。现行为：`"*"` + system_admin → 直接 `CrossTenant` + `SYSTEM_CROSS_TENANT_QUERY` audit；system_admin 显式 switch 非 home 租户 → 放行 + `SYSTEM_TENANT_SWITCH` audit。**零 block 条件、无任何开关**。`AuditAction` 为 protocol 单份 StrEnum（加值时仍按惯例全仓 grep 防双份漂移）。

### 9.2 设计

#### 9.2.1 HX-8a 存储 — 姊妹表 `tenant_provider_secret` / `tenant_tool_secret`（迁移 0073）

| 列 | 类型 | 说明 |
|----|------|------|
| `tenant_id` | UUID NOT NULL | 复合 PK 首列 |
| `provider` / `tool` | TEXT NOT NULL | 复合 PK 次列；值域同平台表 |
| `secret_ref` | TEXT NOT NULL | `secret://` / `kms://` ref，沿用 `validate_secret_ref` |
| `enabled` | BOOL NOT NULL DEFAULT true | 语义见 HX-H2 |
| `created_at` / `updated_at` / `updated_by` | 同平台表 | 运营审计三件套 |

- RLS：**ENABLE + 标准 tenant policy**（纵深防御——即便误用租户上下文直查也只见己行，行内只有 ref 无真值）；平台域读写一律 `bypass_rls_session()`（与 service 现行读法一致）。
- 行语义：**行存在 = 该租户该 key 有 override**；删行 = 回 fallback 平台视图。

#### 9.2.2 service 合并视图 — `PlatformSecretsService` 扩展

新增 `effective_provider_credentials_for(tenant_id)` / `effective_tool_credentials_for(tenant_id)`，合并序：

```
env seed → platform DB rows（DB wins；disabled→suppress，P-12 现状）
        → tenant rows（enabled→override 该 key；disabled→suppress 该租户该 key；无行→平台视图原样）
```

- 缓存：`_reload()` 同轮全量加载租户行为 `dict[tenant_id, dict[key, row]]`（行数 = O(租户 × override)，运营手工配置量级，全量缓存无压力）；TTL 30s 与 `invalidate()` 与现缓存共用。
- 平台无租户行为不变：`effective_*_credentials()`（无租户参数）保留原语义，现调用零改动。

#### 9.2.3 resolver 接线 — 可选 tenant-aware getter（零签名破坏）

`CredentialsResolver` 构造新增可选 kwargs：

```python
tenant_provider_getter: Callable[[UUID], Awaitable[dict[Provider, str]]] | None = None
tenant_tool_getter: Callable[[UUID], Awaitable[dict[Tool, str]]] | None = None
```

`resolve_provider/resolve_tool`：tenant getter 存在 → 直接取其返回的**最终合并视图**（合并逻辑全收在 service，HX-H3）；不存在 → 现路径字节不变。control-plane lifespan 把 `platform_secrets_service.effective_*_credentials_for` 接进来。现有 doubles（含 tools/eval）不传新参即不受影响——按协议 sweep 纪律全仓 grep 验证。

> **实施修订（PR1，2026-06-12）**：`helix-common/credentials` 路径为 harness 禁写区，"resolver 加可选 kwargs" 不可实施。等价替代：control-plane 新模块 `tenant_secret_overlay.py` 的 **`TenantOverlayCredentialsResolver(CredentialsResolver)` 子类**——override `resolve_provider/resolve_tool` 直查 tenant-effective 视图（错误契约与基类 platform 路径逐字段一致：`mode="platform"` + kind + key），`app.py` 构造点换子类。helix-common 零接触，合并逻辑仍全收 service——HX-H3 语义不变且更纯。

#### 9.2.4 管理 API — `/v1/platform/credentials/tenants/*`（system_admin only）

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/v1/platform/credentials/tenants/{tenant_id}` | 该租户 override 清单 + 每 key effective 来源标注（tenant/platform/env/unset） |
| PUT | `.../tenants/{tenant_id}/providers/{provider}`、`.../tools/{tool}` | upsert override：复用 `PlatformSecretWrite`（`value` 写穿 SecretStore → `secret://tenant-{tenant_id}-{kind}-{name}` 命名空间 ref；或直给 `secret_ref`）+ `enabled` |
| DELETE | 同 PUT 路径 | 删 override，回 fallback（204） |

- 复用 `_authz` system_admin 门 + `_emit_platform_audit` 同族新 action（`PLATFORM_PROVIDER_CREDENTIAL_TENANT_UPSERT/DELETE`、`PLATFORM_TOOL_CREDENTIAL_TENANT_UPSERT/DELETE`，details 带 `tenant_id`，真值不进 audit）+ 写后 `invalidate()`。
- 总目录 `GET /v1/platform/credentials` 每 key 附 `tenant_override_count`（轻量计数，不展开行）。
- 校验：`tenant_id` 必须是存在的租户（404 否则）；provider/tool 值域校验同平台端点。

#### 9.2.5 UI — `SettingsPlatformConfig` 行级抽屉

每 provider/tool 行加「租户 override」入口（计数徽标 → 抽屉）：抽屉内列该 key 的全部租户 override（租户名 + ref 来源 + enabled），支持增（选租户 + 粘贴 value/ref）/改/删。system_admin only（页面已是）；跨租户 scope 语义不适用（平台域页面）。i18n 双语 + Storybook + vitest 照 SE-8 清单。**不复活 `SettingsTenantCredentials` 编辑面**（HX-H5）。

#### 9.2.6 HX-8b — 部署级跨租户 block 开关

- settings：`cross_tenant_query_enabled: bool = True`（`HELIX_CONTROL_PLANE_CROSS_TENANT_QUERY_ENABLED`，default 保现状）。
- `ensure_tenant_scope` 新增可选 kw `cross_tenant_enabled: bool = True`；14 个调用点机械 sweep 传 `settings.cross_tenant_query_enabled`（settings 经既有 `request.app.state.settings` 惯例获取）。
- `false` 时（开关语义 = **system_admin 不得越出 home tenant**，HX-H4）：
  - `tenant_id="*"` → 403 `{"code": "CROSS_TENANT_DISABLED"}` + audit `SYSTEM_CROSS_TENANT_BLOCKED`；
  - system_admin 显式 switch 非 home 租户 → 同样 403 + 同 audit（details 区分 `mode: aggregate|switch`）；
  - 普通租户单租户路径零接触。
- 新 `AuditAction.SYSTEM_CROSS_TENANT_BLOCKED = "system:cross_tenant_blocked"`（protocol 单处 + 全仓 grep 双份漂移点）。
- admin UI 不做开关感知适配：403 错误信息已清晰，部署级开关由运营掌控（边界 §9.3）。

### 9.3 边界（不做）

- **per-tenant opt-out**（租户声明数据不参与跨租户聚合）：需 14 端点查询层全量加排除过滤，数据主权需求出现再立项；本次只做部署级。
- **租户自助 BYOK**：Y-1 已否决，不回潮——override 的配置主体永远是平台 system_admin。
- **上游 key 自动分配/轮换**：override 是手动运营操作；按用量自动开专用 key 属 Stream Y 计量后续。
- **跨 replica 缓存失效**：沿用 `PlatformSecretsService` TTL 30s 边界（M0/M1 单实例可接受，注释已在册）。
- **admin UI 对 block 开关的感知**（隐藏 `⋆` scope 选项等）：部署级开关场景下 403 即足；UI 适配等真实需求。

### 9.4 可观测

- 管理操作：4 个新 audit action（§9.2.4）全覆盖增删改，details 带 `tenant_id`/`enabled`/ref 来源（无真值）。
- blocked 尝试：`SYSTEM_CROSS_TENANT_BLOCKED` audit + `tenant_scope.cross_tenant_blocked` 结构化日志。
- `helix_platform_credentials_tenant_overrides` gauge（service `_reload()` 时 set，租户 override 总行数）——运营一眼看 override 面有多大。
- resolve 热路径不加新指标：缓存命中路径，治理可观测由 audit 承担。

### 9.5 测试

- **PR1**：迁移 + store CRUD + RLS 隔离（真 PG integration：租户上下文只见己行/bypass 见全量）+ service 合并矩阵（env/platform/tenant × enabled/disabled/无行 fallback，含 tenant disabled suppress 不回落）+ resolver tenant getter 命中/缺省两路 + 现有 doubles 全量回归（grep sweep 含 tools/eval）。
- **PR2**：API（upsert value 写穿 ref 形态/直给 ref 校验/delete 回 fallback/租户 404/非 sysadmin 403/audit 断言含 tenant_id 无真值/invalidate 即时生效）+ UI vitest（抽屉增删改）+ Storybook + Playwright 冒烟。
- **PR3**：`ensure_tenant_scope` 单元矩阵（开关 off × {"*", switch, home, 普通用户} 四象限）+ 端点集成抽测（off 时 403 + blocked audit；on 时全量回归零变）。

### 9.6 Mini-ADR

- **HX-H1 姊妹表而非改现表 PK**：`platform_*_secret` PK 单列文本、无预留 tenant_id；改 PK = 破坏式迁移 + 全 store/service 适配 + NULL 复合唯一坑（PG 默认 NULLS DISTINCT）。姊妹表零迁移风险、行语义自明（行存在 = override），rate_card 的"预留列"路线对已存在的表不成立。
- **HX-H2 disabled 租户行 = suppress（镜像 P-12）**：fallback 仅在"无租户行"时发生；`enabled=false` 是显式治理动作（关停某租户某 key），不是"暂时回平台"——语义与平台行 disabled suppress 完全对齐，admin 心智模型一套。顺带兑现租户级禁用粒度，零额外结构。
- **HX-H3 合并逻辑收在 service，resolver 只换 getter**：resolver 保持纯解析器（Y-1 后它已不含策略）；可选 kwargs 零签名破坏，doubles 不强制 sweep；getter 返回最终视图，resolver 不知道"override"概念。
- **HX-H4 block 开关语义 = system_admin 不得越出 home tenant**：只拦 `"*"` 聚合会留显式逐租户 switch 后门，合规意图（私有部署禁用平台越权读）即落空；default `true` 保现状，关闭是显式部署决策。
- **HX-H5 UI 挂 `SettingsPlatformConfig`**：override 是平台运营操作，归平台凭证页；`SettingsTenantCredentials` 是租户视角只读视图（Y-1 削减结果），复活其编辑面 = 视觉上的 BYOK 回潮，语义不回退。

### 9.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §9 + ITERATION-PLAN 细化（2 PR → 3 PR 修正） | 纯 docs，CI |
| PR1 | 迁移 0073 + tenant secret store + service 租户合并视图 + resolver tenant getter + runtime 接线 + overrides gauge | §9.5-PR1 |
| PR2 | 管理 API 5 端点 + 4 audit action + SettingsPlatformConfig 抽屉 + SE-8 接线 | §9.5-PR2 |
| PR3（收尾） | settings 开关 + ensure_tenant_scope block + SYSTEM_CROSS_TENANT_BLOCKED + 14 调用点 sweep + 文档同步 | §9.5-PR3；零债 6 条 |
