# Stream HX — Harness 强化（设计先行）

> **背景**：2026-06-10 完成 helix harness 十维能力评估（`docs/research/2026-06-10-helix-harness-capability-assessment.md`），用户拍板把全部非强项做强，按 Wave 严格顺序推进（Wave 1 零债收完再进 Wave 2）。每条子项设计先行 + 零债收尾（Stream CM 同节奏）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条子项 PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **横切公理**（HX-12/13 立项时锁定，对全 Stream 生效）：① 不存在 drop core tool / drop 历史真相的代码路径（视图级裁剪 ≠ 状态删除）；② fail-open——基础设施故障的代价只能是多花 token，绝不能是少能力；③ config 防御解析（clamp / safe default，不 raise）。
>
> **本文件状态**：Wave 1（HX-1~4，§2-§5）已全部交付。Wave 2（2026-06-11 起）：HX-5（§6）已交付；HX-6（§7）/ HX-7（§8）/ HX-8（§9）/ HX-12（§10）/ HX-13（§11）已交付；HX-10（sandbox 安全纵深，§12）已交付（Wave 3 首项，#576/#577/#578/#579）——gVisor 生产上线前置 follow-up 均已决：F1（沙箱→proxy 寻址）方案=`/etc/hosts` 固定 IP（§12.2.5，接缝 #592 + CI 双 runtime 转正 gate_49 + compose 落静态 IP，剩纯运维选私网段），F2（fork-bomb 语义）方案 A「沙箱阵亡+重建」（§12.2.4，gate_56 转正）；HX-9（租户级出站 webhook hook，§13）已全交付（#595 设计 / #596 数据模型 / #597 API+admin-ui / #598 投递引擎 / #待填 入队扫描收尾）。其余各条开工时追加章节。

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
| HX-10 | ⑤ 沙盒 | misconfig 无测试钉死（实证 LLM 100% 逃逸类）；无 seccomp（吃宿主 Docker 默认）；gVisor 一键可切但从没真跑过验证；CI 零镜像 CVE 扫描 | misconfig 断言 + 仓库自管 pinned seccomp profile + gVisor 可配置+CI 真验证 + Trivy 分镜像门禁+周扫（实证驱动，[research](../research/2026-06-12-sandbox-isolation-defense-in-depth.md)） | §12（本文） |
| HX-12 | ② 工具面 | find_tools 检索无 ranked/中文分词；MCP always-defer 无逃生门；deferred 直调裸报错；promotion 永不退场 | BM25+jieba ranked 检索 + 防呆包 + 阈值逃生门 + call-through + 退场 | §10（本文） |
| HX-13 | ② 工具面 | 厂商原生档（anthropic defer_loading / openai allowed_tools）未接；caller 链路无 deferred 可见性 | `tool_disclosure` 能力位 + `ToolSpec.defer_loading` 单标记 + 两家接线（前置 HX-12） | §11（本文） |

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

---

## 10. HX-12 — 工具披露 2.0·应用层

> 源头：[Hermes Tool Search 源码分析](../research/2026-06-11-hermes-tool-search-source-analysis.md) + 业界调研，2026-06-11 拍板（应用层 B 先行——唯一覆盖全部 9 provider 的层；HX-13 厂商原生档紧随）。横切公理：**不存在 drop core tool 代码路径**；**fail-open（故障 = 多花 token，绝非少能力）**；config 防御解析。

### 10.1 现状取证（2026-06-12，main@588d6fa）

1. **检索**：`registry.search`（`orchestrator/tools/registry.py:369`）三语法（`select:a,b` 精确 / `+kw rest` AND 过滤 / 其余 regex→substring 降级），**只搜 deferred 池**，无 ranked、无中文分词、无相关性排序——多工具命中时顺序无意义。
2. **MCP always-defer 硬编码**（TE-6b）：三个 MCP 池（平台/租户/用户 OAuth）在 `tools/assembly.py:489/517/539` 全部 `deferred=True`，**无"总量小就直接 active"逃生门**——一个只挂 3 个 MCP 工具的 agent 也要付 find_tools 间接层成本。
3. **find_tools**（`tools/find_tools.py:28`）：描述未截断（超长 MCP description 全文进上下文）；零命中返回 `"(no matching tools found)"` 无任何引导；promote 经 `state_updates={"promoted_tools": names}`。
4. **dispatch**（`graph_builder/builder.py`）：~~LLM 直调未 promote 的 deferred 名 → 裸报 unknown~~ **取证修正（PR3，2026-06-12）**：`get_required` 查 `_tools`（含 deferred）——deferred 名直调**本就能执行**（TE-6 "stays dispatchable" 设计如此）。真实缺口：①执行成功但**不 promote**（schema 不进后续 bind，模型每次都得盲调）；②真未知名（拼错/幻觉）报错无建议。
5. **promoted_tools reducer append-only**（`state.py:46` `_merge_promoted`）：union-dedupe，**永不删除**——长会话里 promote 过的工具 schema 永久占上下文，无退场。
6. **HX-1 资产可用**：`TokenEstimator` 协议 + `TiktokenEstimator`（`helix-runtime/tokens.py`，`default_estimator()` 单例）已落地；`context_window` 解析（manifest override → MODEL_CATALOG）在 agent_factory。阈值逃生门可直接用真 tokenizer（ITERATION-PLAN 原文"chars/4，HX-1 落地后换"已过期——直接上真分词）。
7. **jieba 已是仓内依赖**（helix-persistence，J.5 `knowledge/text_search.py` app-side CJK 分词先例）；无 BM25 实现/依赖。
8. **指标**：`helix_tool_call_total{tool,outcome}` + `helix_tool_latency_seconds{tool}` 已在（builder.py:143）；promotion 域零指标。

### 10.2 设计

#### 10.2.1 ① ranked 检索内核（registry.search 自然语言模式）

- **三语法保留**（`select:` / `+kw` / regex 先试——既有调用语义零破坏）；非语法命中的自然语言 query 走 **BM25 ranked top-K（K=8）**。
- 语料：每个 deferred 工具 = 名字拆词（`_`/`-`/camelCase 切分）+ description + 顶层参数名；**jieba 分词**（J.5 同款 app-side 先例，中文 query/描述都正确切词）。
- 索引：注册期增量建（`register`/un-defer 时失效重建 lazy），工具量级几百，内存倒排足够。
- **零 IDF 兜底**：query 与语料零重叠（BM25 全零分）→ 退回现 substring 路径——检索永不比现状差（fail-open）。
- **向量接缝**：检索内核收在独立函数 `rank_tools(query, corpus) -> list[(name, score)]`，签名不含 BM25 概念——将来换 embedding 检索是函数替换，不动 search 外壳。不建 Protocol（单实现不抽象）。
- 依赖：`rank-bm25`（纯 Python，PyPI 成熟）+ `jieba` 进 orchestrator。

#### 10.2.2 find_tools 防呆包

- 结果行带 **source 标注**：registry.register 加可选 `source: str | None`（assembly 注册时传 `mcp:<server>` / skill 路径传 `skill`；缺省 builtin）——模型看得见工具来路。
- **描述截 400 字符**（Hermes 同款）：超长 MCP description 截断 + `…`，全文留 registry 内部（dispatch 校验不受影响）。
- **教学式错误**：零命中返回改为含三语法用法 + "try a broader natural-language query" 的引导文案（不再是裸 `(no matching tools found)`）。
- **docstring 过期修正**：find_tools spec.description 重写——补自然语言 ranked 模式说明。
- 零命中打 `helix_tool_promotion_total{event="miss"}`（治理信号，将来喂 HX-2 学习闭环）。

#### 10.2.3 ② 阈值逃生门（assembly 注册期）

- `register_mcp_tools` 注册完三池后（或注册时聚合）：用 `default_estimator()` 估算**全部 MCP 工具 schema**（name+description+parameters JSON）token 总量；总量 < **min(context_window × 10%, 20k)** → 全部重注册为 active（`registry.register(tool)` 覆盖即 un-defer，现成语义），find_tools 自然不再注册（`has_deferred()` 为假）。
- `context_window` 由 agent_factory 既有解析传入 assembly（加参数）。
- 防御：estimator 异常 → 维持 always-defer 现状（fail-open 到行为不变侧）。
- **实施注记（PR2）**：`context_window=None`（未传参的 legacy 调用点/测试）同样**不启用**逃生门——TE-6b 行为字节不变；唯一生产调用点 agent_factory 显式传 `_resolved_context_window` 启用。
- 阈值不开配置面（常量 + 注释），有真实需求再参数化。

#### 10.2.4 ③ call-through（dispatch 拦截）

- dispatch 成功路径检测：名字**在 deferred 池** → 执行照常（本就 dispatchable）+ **补写 promote**（outcome 的 state_updates 合并 `promoted_tools`，重复 promote 由 reducer dedupe 幂等）+ `helix_tool_promotion_total{event="call_through"}`——模型记得工具名就不必付 find_tools 往返，且 schema 进入后续 bind 不再盲调。
- 名字**不在任何池** → 错误信息附 **ranked top-3 建议**（复用 10.2.1 内核检索错误名）：`"unknown tool: 'x'. Did you mean: a, b, c? Use find_tools to search."`。
- 公理兑现：两分支都只多花 token，不可能比现状（裸报错）差。

#### 10.2.5 ④ promotion 退场 + 指标

- state 新增 `promoted_tool_last_used: dict[str, int]`（name → step_count，reducer 按 key 取 max）；tools_node 每次成功 dispatch promoted 工具时打点。
- **compressor 触发时**（既有 should_compress 判定点）：`step_count - last_used > N`（N=12 常量）的 promoted 工具降级——`promoted_tools` reducer 升级为**带删除语义**：new 值支持 `{"add": [...], "remove": [...]}` dict 形态（list 形态保持现 add 语义零破坏，dict 由降级路径专用）。
- 降级 = 从 promoted 列表移除（下 turn bind 不含其 schema）；工具仍在 deferred 池，find_tools / call-through 随时可再召回——**退场永不丢能力，只省上下文**。
- 指标：`helix_tool_promotion_total{event}`，event ∈ {promote, call_through, demote, miss}；现有 `helix_tool_call_total` 不动。

### 10.3 边界（不做）

- **向量检索**：接缝已留（10.2.1），embedding 索引另立项。
- **厂商原生档**（anthropic defer_loading / openai allowed_tools）：HX-13（前置本项）。
- **core tool 裁剪**：任何 manifest 声明的 active 工具不进退场范围——降级只作用于 promoted-from-deferred 集合（公理）。
- **退场 N 的自适应**：常量起步，有命中率数据再谈（HX-6 EWMA 同款纪律）。
- **find_tools 结果分页**：top-K=8 截断 + 教学文案足够。

### 10.4 可观测

- `helix_tool_promotion_total{event=promote|call_through|demote|miss}`（10.2.5）。
- 既有 `helix_tool_call_total{tool,outcome}` / `helix_tool_latency_seconds` 不动；call-through 的执行打点走既有 outcome 维度。
- 阈值逃生门触发：assembly INFO 日志（一次性，启动期）。

### 10.5 测试

- **PR1**：rank_tools 单测（中文/英文/混合 query 排序、零 IDF 兜底、名字拆词）+ search 三语法回归零变 + find_tools 防呆（截断/教学错误/source 标注/miss counter）。
- **PR2**：逃生门矩阵（总量小→全 active + find_tools 不注册 / 超阈值→维持 defer / estimator 异常→维持 defer / context_window 传递）。
- **PR3**：call-through（deferred 名直调→promote+执行+counter / 未知名→top-3 建议文案 / active 工具路径零变）。
- **PR4**：退场（last_used 打点 / N turn 未用→demote+counter / reducer dict 删除语义 + list 兼容 / demote 后 find_tools 可再召回 / core tool 永不进退场）。

### 10.6 Mini-ADR

- **HX-I1 BM25 + jieba 进 orchestrator，不自研检索**：rank-bm25 纯 Python 成熟库；jieba 已是仓内依赖（J.5 先例）；工具语料量级几百，内存索引零运维。
- **HX-I2 检索内核留函数级向量接缝，不建 Protocol**：单实现不抽象（CLAUDE.md 简单优先）；`rank_tools` 签名与 BM25 解耦即可替换。
- **HX-I3 逃生门用真 tokenizer（HX-1 资产），阈值 min(10% ctx, 20k) 常量**：估算失败 fail-open 到 always-defer 现状侧。
- **HX-I4 call-through 本 turn 执行**：registry.get 对 deferred 工具本就可 dispatch（TE-6 设计如此），拦截只是把"报错"换成"promote+执行"——零新攻击面（工具本身已过 manifest/许可注册）。
- **HX-I5 reducer 删除语义 = dict 形态扩展**：list 值保持 append-only 旧语义（全部既有写点零改动）；`{"add","remove"}` dict 仅退场路径使用；demote 不出 deferred 池，能力永不丢失。

### 10.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §10 + ITERATION-PLAN 细化 | 纯 docs，CI |
| PR1 | ranked 检索内核（BM25+jieba）+ find_tools 防呆包 + miss counter | §10.5-PR1 |
| PR2 | 阈值逃生门（assembly + estimator + context_window 传入） | §10.5-PR2 |
| PR3 | call-through + ranked 错误建议 | §10.5-PR3 |
| PR4（收尾） | promotion 退场（last_used + reducer 删除语义 + demote）+ 指标收口 | §10.5-PR4；零债 6 条 |

---

## 11. HX-13 — 工具披露 2.0·厂商原生档

> 前置 HX-12（§10，已收尾）。拍板（2026-06-11）：能力位翻译层（CM-10 同款模式）；三档行为分裂已接受（None 兜底档 = HX-12 全套保语义底线）；beta 拒绝 fail-open 降 None 档。

### 11.1 现状取证（2026-06-12，main@HX-12 #570 收尾后）

1. **ModelEntry**（`protocol/model_catalog.py:25`）：frozen Pydantic + `extra="forbid"`，现 8 字段；**CM-10 先例**：`thinking: Literal["effort","budget","toggle"] | None = None` 能力位 + `agent_factory._thinking_payload` 翻译层 + per-provider 分支——HX-13 完全同模式。新字段必须带默认值（50+ catalog 条目 + 测试 doubles 才零破坏）。
2. **Provider 值域 9 家**（`provider_catalog.py:29`）：anthropic / openai / azure / self-hosted / kimi / glm / deepseek / qwen / doubao。**无 openrouter**（立项文案里的 openrouter 不在本仓值域，剔除）；compat 家族（kimi/glm/deepseek/qwen/doubao/self-hosted）`tool_choice.allowed_tools` 支持情况按 CM-L5 纪律逐家核实，核实不过 → None 档。
3. **anthropic adapter**（`llm/providers/anthropic.py`）：自写 httpx 客户端（非 SDK）——beta header 是 `HTTPAnthropicClient.messages` headers 字典加一行的事；`_to_anthropic_tool` 输出 name/description/input_schema 三字段。现无任何 beta header 机制（CM-9 字段全 GA）。
4. **openai adapter**（`llm/providers/openai.py`）：7 家共用 `OpenAIProvider`；`_to_openai_tool` 输出 function 三件套；**现无 `tool_choice` 处理**。
5. **关键架构事实**：`LLMCaller` Protocol = `(messages, tools)`——caller **无 registry / deferred 全集可见性**；但 `agent_node` 的 bind 组装点（builder.py）持有 registry 闭包 + promoted state——disclosure 分支天然落位在 agent_node 层，caller 接口不必扩展。
6. **HX-12 资产咬合**：anthropic native search 由服务端检索并直接发起 deferred 工具的 tool_call——dispatch 侧 **call-through（HX-12 PR3）已兜住**（直调 deferred 名 → promote + 执行），原生档检索结果回流 promote 零额外代码。

### 11.2 设计

#### 11.2.1 ① 能力位 + 单一传载标记

- `ModelEntry.tool_disclosure: Literal["native_search", "allowed_tools"] | None = None`。标注：anthropic 支持 tool-search beta 的条目 → `native_search`；openai / azure → `allowed_tools`；其余默认 None（compat 家族核实结论以注释入 catalog）。
- `ToolSpec.defer_loading: bool = False`（dataclass 默认值，全部构造点零破坏）：**一个标记服务两档**——
  - anthropic 档：标记者以 `defer_loading: true` 进 API（服务端检索池）；
  - openai 档：标记者**排除**出 `tool_choice.allowed_tools` 子集（全量 schema 冻结，允许集 = 未标记者）。
- agent_node 按档构造 bind：
  - **None 档**：现状（active + promoted），字节不变。
  - **native_search 档**：active（**排除 find_tools**——服务端检索取代）+ promoted + deferred 全集的 `dataclasses.replace(spec, defer_loading=True)` 副本。
  - **allowed_tools 档**：active（**保留 find_tools**——它是 promote 的唯一入口，allowed_tools 强约束下模型无法直调名单外工具）+ promoted + deferred 全集（未 promote 者标 defer_loading）。schema 全量冻结利好 provider prompt cache；promotion 驱动**子集**而非 bind。
- 档位解析：agent_factory `catalog_entry(provider, name).tool_disclosure` → `build_react_graph(tool_disclosure=...)` 新可选参数（None 默认零破坏）。

#### 11.2.2 ② anthropic 接线

- `_to_anthropic_tool`：spec.defer_loading 为真时输出 `"defer_loading": true`。
- `HTTPAnthropicClient.messages`：请求含 defer_loading 工具时加 header `anthropic-beta: tool-search-tool-2025-10-19`（provider 检测 `any(defer_loading)` 决定，header 不常驻）。
- **fail-open 降级**：beta 被拒（4xx 含 beta/unsupported 信号）→ 重发一次"去 defer 标记 + 去 header + 全量工具"的请求，并进程内记忆降级（该 provider 实例此后直接走 None 档形态，避免每请求双发）；任何判定不确定 → 降级侧。模型方挂掉 ≠ 能力丢失（公理）。

#### 11.2.3 ③ openai / azure 接线

- `OpenAIProvider.complete`：存在 defer_loading 标记时构造 `tool_choice` 形态 `{"type": "allowed_tools", "mode": "auto", "tools": [未标记者]}`（OpenAI 2025 allowed_tools 语法；azure 同 wire 格式）。
- 不支持 `allowed_tools` 的 compat 家族（核实结论）维持 None 档：catalog 不标注即天然走兜底，**无运行时探测**（CM-L5：能力位是声明式的，错了改 catalog 一行）。
- fail-open：tool_choice 被拒 → 同 anthropic 模式降级重发 + 记忆。

### 11.3 边界（不做）

- **openrouter**：不在 Provider 值域，不虚构。
- **compat 家族的 allowed_tools 探测**：声明式 catalog（CM-L5），不做运行时 capability probe。
- **anthropic SDK 迁移**：自写 httpx 客户端加 header 足够，不为 beta 换 SDK。
- **native 档的 find_tools 混合模式**：服务端检索与自家 find_tools 二选一，不并存（双检索通道徒增模型困惑）。
- **per-tenant 档位 override**：能力位是模型属性不是租户策略。

### 11.4 可观测

- `helix_tool_promotion_total` 四事件继续工作（native 档 call-through 即服务端检索回流的 promote 信号）。
- 降级事件：`helix_llm_tool_disclosure_fallback_total{provider}` counter + WARNING 日志（一次性）。

### 11.5 测试

- **PR1**：ModelEntry 新字段默认值（catalog 全量构造回归）+ ToolSpec.defer_loading 默认 False + doubles sweep（grep ModelEntry( 构造点全量）。
- **PR2**：anthropic——defer_loading 进 wire / beta header 条件出现 / 无标记时 header 不出现 / beta 拒绝降级重发 + 记忆 + counter / native 档 bind 排除 find_tools 含 deferred 副本（agent_node 分支测试）。
- **PR3**：openai——tool_choice 子集构造 / 全量 schema 冻结断言 / promoted 进 allowed / find_tools 在 allowed / None 档零 tool_choice 回归 / 降级路径。

### 11.6 Mini-ADR

- **HX-J1 disclosure 分支落位 agent_node，caller 接口不动**：bind 组装点本就持有 registry + promoted；`LLMCaller` Protocol 扩展会迫使全部实现者 sweep——用 ToolSpec.defer_loading 单标记把档位语义随 specs 自然下传。
- **HX-J2 一个 defer_loading 标记服务两档**：anthropic 读它进服务端检索池，openai 读它反推 allowed 子集——两档语义同源（"这工具暂不进活跃面"），无需两套标记。
- **HX-J3 find_tools 档位差异**：native 档排除（服务端检索取代，双通道徒增困惑）；allowed_tools 档保留（强约束下它是唯一 promote 入口）。
- **HX-J4 fail-open = 降级重发 + 进程内记忆**：beta/tool_choice 拒绝 → 一次性回 None 档形态，此后该 provider 实例直走兜底——每请求双发不可接受，重启即重试新档。
- **HX-J5 能力位声明式（CM-L5 延续）**：catalog 标注即真相，不做运行时探测；compat 家族核实结论写注释，错了改一行。

### 11.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §11 + ITERATION-PLAN 细化 | 纯 docs，CI |
| PR1 | ModelEntry.tool_disclosure + catalog 标注 + ToolSpec.defer_loading + 档位解析至 build_react_graph | §11.5-PR1 |
| PR2 | anthropic：agent_node native 档分支 + adapter defer_loading/beta header + fail-open 降级 | §11.5-PR2 |
| PR3（收尾） | openai/azure：allowed_tools 子集 + compat 核实结论入册 + 降级 + 文档 | §11.5-PR3；零债 6 条 |

---

## 12. HX-10 — sandbox 安全纵深：misconfig 钉死 + seccomp + gVisor + Trivy

> 评估维度 ⑤（沙盒）。定位：**多租户共享宿主下 agent 执行 LLM 生成代码的隔离纵深**。
>
> **实证驱动设计**：本章方向经四角业界调研锁定，依据全文落 [`docs/research/2026-06-12-sandbox-isolation-defense-in-depth.md`](../research/2026-06-12-sandbox-isolation-defense-in-depth.md)。核心实证：① 业界共识隔离强度匹配信任画像，多租户 untrusted 代码 → gVisor/microVM；② gVisor「2-3x 损失」被夸大（CPU-bound Python <3%，Tencent 百万级 agentic-RL 沙箱生产验证兼容问题 1.7%）；③ systrap 平台无需 KVM，国内云标准 ECS 可跑（阿里云 OpenSandbox / 腾讯云生产已用）；④ **SANDBOXESCAPEBENCH（ICML 2026）**：前沿 LLM 对容器逃逸——misconfig 类 100% 成功，内核 CVE 类 hardened 下 ~40%。
>
> **威胁模型（实证修正，写入横切公理脚注）**：内部可信 vs 对外开放，逃逸**能力相同**，差的只是入口向量 + 攻击者迭代次数。多租户共享宿主下逃逸 = **跨租户数据泄露**。**所需隔离强度由跨租户爆炸半径决定，不由用户信任决定**——信任只降概率不改影响。决策（2026-06-12，方案 1）：gVisor 当期纳入（可配置 + CI 验证），不缓做。
>
> 横切公理边界澄清：fail-open 公理（基础设施抖动只多花 token 不减能力）的例外是**安全配置事故**——seccomp profile 配了却加载不了、misconfig 断言失败，必须 fail-closed（拒启动 / 拒 merge）。这不是抖动，是配置错误，静默放过等于无防护。

### 12.1 现状取证（2026-06-12，main@aa58e4a）

| 事实 | 证据 | 判定 |
|------|------|:----:|
| runtime hardening 全集中一处：read-only / cap-drop ALL / no-new-privileges / pids/mem/cpu / --internal 网络 | `runtime_provider.py:82-105` | seccomp 加 `--security-opt seccomp=` 一行即落位 |
| docker.sock 挂在 **supervisor** 不在 sandbox；sandbox cap-drop ALL + read-only + no-new-privileges + tmpfs 工作区 | `Dockerfile`、`runtime_provider.py:88-93,109-122`、`docker-compose.yml` | **三个 100%-逃逸 misconfig 类（socket/特权/可写宿主挂载）应都不中**——但无测试钉死（gap ⓪） |
| **无任何 `--security-opt seccomp` 参数** → runc 下吃宿主 Docker 默认 profile（随版本漂移） | `runtime_provider.py` 全文 grep 无 seccomp | 真 gap ①：隔离强度由宿主 Docker 版本决定 |
| runc↔runsc 一键可切，但**从没在 runsc 下真跑过测试**；集成测试 4 处硬编码 `oci_runtime="runc"` | `runtime_provider.py:103-104`、`test_supervisor_integration.py:205/207/528/530` | 真 gap ②：「切 gVisor 照常工作」未验证；Gate #6 timing / #7 CVE-2019-5736 ⏳ 卡在「需真 runsc」 |
| CI 零镜像扫描：office 有构建+推送 workflow 不扫；minimal 连 CI 构建都无 | `.github/workflows/sandbox-image-office.yml`、`docker-compose.yml:313` | 真 gap ③：基础镜像 CVE 全程无感 |
| 两镜像基底：minimal=alpine、office=debian-slim+LibreOffice/CJK（CVE 面大） | 两 Dockerfile | office 卡 HIGH 会恒红（§调研3） |

### 12.2 设计

#### 12.2.0 ⓪ misconfig 断言（PR1，最高优先级、零成本）

实证里前沿 LLM 对三类 misconfig **100% 逃逸成功**——这是比任何 runtime 升级都重要的防线，且是 config 属性非运行时开销。我们取证三项都不中，但**必须钉死防回归**：

- `runtime_provider` 单测断言 docker run argv：**不含** `-v /var/run/docker.sock`（任意形式）/ `--privileged` / `--cap-add`；**含** `--cap-drop ALL` + `--read-only` + `--security-opt no-new-privileges`。
- 工作区挂载断言：tmpfs 或 named volume，**绝不**宿主路径 bind mount（`-v /host/path:...`）。J.15 持久卷是 docker named volume（`workspace_volume:/workspace`），非宿主路径，合规。
- 任一断言失败 = 测试红 = 阻 merge（fail-closed）。

#### 12.2.1 ① seccomp pinned profile（PR1）

- **profile 入仓** `infra/sandbox-image/seccomp-profile.json`：以 Docker 官方 default profile 为基线，显式收紧高危族移出 allowlist：`io_uring_setup`/`io_uring_enter`/`io_uring_register`、`userfaultfd`、`keyctl`/`add_key`/`request_key`、`bpf`、`perf_event_open` 之外的内核调试族。每条带注释（CVE/利用类别）。
- **实证修正（不收紧的）**：
  - **`clone3` 保留允许**——Docker default 本就允许；显式 `ERRNO` 会让新版 glibc/Python（用 clone3 建线程）崩溃。default profile 对未知 syscall 返 `ENOSYS` 让 glibc 回退 clone，profile 必须保持此 default 动作。
  - **`perf_event_open` 移出强制收紧**——禁它使 py-spy/cProfile 等 profiling 失效，安全收益边际；保留允许，仅注释标注。
  - **`io_uring*` 保留禁用但文档标注兼容代价**——禁它砍掉逃逸重灾区（安全收益实打实），但用 io_uring 的新 async 库会收 ENOSYS 回退 epoll（Claude Code #27230 Bun 实例）；`infra/sandbox-image/README` 标注「沙箱内 io_uring 不可用」。
- **传载**：`SandboxRuntimeProvider` 增 `seccomp_profile_path: str | None`（None = 不加参数 = 现状）；非 None 时 argv 插 `--security-opt seccomp=<path>`（紧邻 no-new-privileges）。runsc 下同传（gVisor 对宿主侧 Sentry 进程仍受益，两轨叠加）。
- **fail-closed 校验**：supervisor lifespan 启动时——配了 profile 路径但文件缺/JSON 非法 → 拒启动（明确异常）。安全配置事故非抖动（横切公理脚注）。
- **settings**：`seccomp_profile_path: str | None = None`，env `HELIX_SANDBOX_SECCOMP_PROFILE_PATH`；默认 None 保 dev/单测零变更，environments yaml 显式开。docker-out-of-docker：路径是**宿主可见路径**（compose volume / 宿主预置），不是 supervisor 容器内路径。

#### 12.2.2 ② gVisor 可配置 + CI 验证（PR2）

实证消除了缓做理由（代价小 + 国内云可跑 + 关闭 40%-可利用残差），故当期纳入，形态 = **可配置 + CI 验证**，非全局无脑强制。

- **部署策略（写入 environments 文档）**：生产多租户同宿主**推荐 runsc**（`HELIX_SANDBOX_OCI_RUNTIME=runsc`）；dev/macOS runc（gVisor Linux-only）。能力代码（runc↔runsc）本就在，本 PR 只补**验证**。
- **新 workflow** `.github/workflows/sandbox-gvisor.yml`：路径过滤触发（`runtime/sandbox/**`、`sandbox-supervisor/**`、`infra/sandbox-image/**`、本 workflow）+ `workflow_dispatch`。
- **步骤**：ubuntu runner → 下载 runsc 二进制（官方源 sha512 校验，版本 pin；**部署注记**：生产经 OSS/ACR 转存分发不依赖 Google 直连，CI 海外直连无碍，systrap 平台无需 KVM）→ 注册 dockerd → 构建 minimal 镜像 → runsc 下跑 sandbox 集成测试。
- **集成测试 runtime 参数化**（PR2 配套）：4 处硬编码 `oci_runtime="runc"` → 读 env `HELIX_TEST_SANDBOX_RUNTIME`（默认 runc，本地/既有 job 零变更）；gVisor workflow 设 runsc。
- **runtime 真起断言**（防静默回落）：容器起后 `docker inspect --format '{{.HostConfig.Runtime}}'` 断言 = 期望值；runsc 期望下实际是 runc 则 fail（daemon 注册失败会静默回落，否则「验证」名存实亡）。
- **兼容坑断言（按 Tencent/调研踩坑清单）**：runsc job 内补——io_uring 在沙箱内返 ENOSYS（确认 seccomp/gVisor 双重不可用且优雅）、`/proc/sys/net` 访问不崩、典型 Python import + numpy 计算跑通。
- **逃逸 PoC 用例（Gate #7 自动化起点）**：CVE-2019-5736 类逃逸「应失败」（runc skip——本就可能受影响；runsc 断言被挡）。timing side-channel（#6）环境敏感留 staging。
  - **实施注记（PR2 #578）**：CVE-2019-5736 真 PoC（覆写宿主 runc 二进制）**降级 staging 渗透**——在共享 CI runner 上跑真容器逃逸利用既不安全（真去破坏宿主）又脆弱（依赖具体 runc 版本/时序）。CI 改以**良性 gVisor 不变量**覆盖：io_uring 在 runsc 下返 -1/ENOSYS（gVisor 不实现，正是 #27230 兼容坑 + 逃逸面收窄证据）+ 其余 gate 验收测试在 runsc 通过（exec/文件/进程/取消隔离），gate_49 网络 + gate_56 fork-bomb 因 gVisor 架构差异 runsc 下 xfail（见下「首跑注记」）。`/proc/sys/net` 与 numpy 项不单测（minimal 镜像 pure-stdlib 无 numpy；gate 测试已覆盖 import/exec 路径），保留 io_uring 这条最高信号不变量。Gate #6/#7 真 PoC 与 timing 同档留 staging（[memory:no-design-choice-disguise] 正面用法——CI 不可安全跑真逃逸是真约束，非弱能力包装）。
- **Gate 收益**：M0→M1 Gate 的 gVisor 验证从一次性人工活动变持续自动验证。
- **首跑注记（PR2 #578，实证驱动）**：gVisor workflow 首跑即暴露 2 个 gVisor 多年 open issue 的架构行为（非测试 bug，调研落 [research](../research/2026-06-12-sandbox-isolation-defense-in-depth.md) + google/gvisor#7469 / #2490）——这正是 gVisor CI 的价值（上线前暴露而非上线后炸）。两者 runsc 下 `xfail(strict=False)` 记录，其余 gate 保留回归保护（不卡 PR、可见、记账，方案 Y）：
  - **gate_49 网络隔离**：gVisor netstack 不支持 docker embedded DNS（127.0.0.11 是 sentry 自身 loopback，dockerd 不在那监听）；容器名解析在 runsc 必失败。隔离本身更强（公网/metadata 全不可达），但**生产沙箱亦靠主机名 `credential-proxy.internal` 找 proxy** → **gVisor 生产上线前置：沙箱→proxy 寻址改 /etc/hosts + 固定 IP（`--add-host`，gofer 文件 gVisor 天然工作）**。**已决：方案=`/etc/hosts` 固定 IP（见下 §12.2.5），CI 已转正。**
  - **gate_56 fork bomb**：`--pids-limit` 在 gVisor 下限 sentry 宿主线程非 guest 进程；fork bomb → Go runtime 建线程失败 → sentry panic → 沙箱（含 runner）整体死（gVisor 从不声称防 fork bomb，资源耗尽防御委托宿主 cgroup）。隔离没破（爆炸半径=沙箱）但语义异于 runc。**gVisor 生产上线前置：接受沙箱阵亡+重建语义 或 guest 内 cgroupfs pids.max + `--memory` 上限**。**已决：方案 A（见下 §12.2.4）。**

##### 12.2.4 HX-10-F2 决策：接受「沙箱阵亡 + 重建」语义（方案 A，2026-06-13）

> 二选一拍板（用户 2026-06-13），经业界实证背书。实证全文落 [research §6](../research/2026-06-12-sandbox-isolation-defense-in-depth.md)。

**选 A：接受沙箱阵亡 + 重建**（否决 B：guest 内 cgroupfs `pids.max` + `--memory`）。

**横切公理（写入决策）**：gVisor **设计上把资源耗尽防御委托宿主 cgroup**——sentry 自身不兜 fork-bomb，资源耗尽 → sentry 死 → 宿主 cgroup 仍限总量。所需防御点在**宿主层限总量**（防溢出到邻居 / 跨租户），**不在 guest 内优雅报错**。F2 不是安全洞，是「错误处理优雅度」；安全纵深目标（爆炸半径锁在沙箱）已达成。

**业界四点背书**（[research §6](../research/2026-06-12-sandbox-isolation-defense-in-depth.md) 详）：
1. **gVisor 官方 Security Model 明文**：「relies on the host resource mechanisms (cgroups) for defense against resource exhaustion and DoS」——委托宿主 cgroup 是设计，非缺陷。
2. **fork-bomb panic 是已知且长期未修**：google/gvisor #2490（fork bomb panic）/ #2489（pids.limit 不生效）/ #3942（runsc 海量 Go 线程）三 issue 2020 年至今 open——gVisor 团队不当 bug 修，正因立场 = 资源耗尽宿主 cgroup 兜、沙箱死重建。
3. **gVisor sandbox 厂商全走 ephemeral + 重建**：Modal（gVisor 跑 untrusted code）明确 ephemeral container lifecycle；GKE Agent Sandbox 用 ephemeral environments + warm pools + Pod Snapshots。
4. **通用共识**：「container isolation doesn't protect against process table exhaustion on the host, so limits must always be set」——防御点在宿主 cgroup 限总量，不在 guest 内报错。

**与我们架构契合**：宿主 cgroup 限总量（爆炸半径锁死沙箱）+ ephemeral 容器 + warm pool（HX-6 已在）+ reaper 重建（已在）= 方案 A 标准件齐全，**零生产代码改动**。用户数据零损失：workspace volume 持久（J.15），fork bomb 只丢一次 in-flight exec。A 唯一让步——恶意用户能 fork bomb 主动弄死自己沙箱触发重建，但那是自残（不放大、不影响邻租户、warm pool 补位）。

**落地（仅测试层固化语义）**：`test_supervisor_integration.py` gate_56——runc 保留「优雅 EAGAIN 遏制」断言（runsc skip）；新增 runsc 专属 `test_gate_56_fork_bomb_sandbox_death_then_rebuild` 断言方案 A：fork bomb 弄死自身沙箱（exec 抛 `SupervisorError`），supervisor 存活 + 全新沙箱重建并执行成功（爆炸半径遏制 + 宿主不受影响）。**gate_56 由 `xfail` 转正为显式 A 语义断言**。gate_49（F1）见 §12.2.5。

##### 12.2.5 HX-10-F1 决策：`/etc/hosts` 固定 IP 寻址（2026-06-13）

> sandbox→credential-proxy 寻址在 runsc 下不能靠 docker embedded DNS。决策前检索业界做法（实证全文落 [research §7](../research/2026-06-12-sandbox-isolation-defense-in-depth.md)）。

**三层解法全部业界背书，我们选的 `/etc/hosts` 固定 IP 是 gVisor 官方钦定的裸 Docker 解**：
1. **gVisor 官方 FAQ 4 个 workaround**：`--network=host`（破隔离）/ `--link`（默认桥破 egress 管控）均否决；**「用 IP 代替容器名」= 我们 `--add-host` 固定 IP**；「上 K8s」见 ③。
2. **规模化玩家上 K8s 问题直接消失**：GKE Sandbox / Ant / Tencent 全在 K8s/containerd，服务发现走 CoreDNS 不经 docker embedded DNS——**这坑是裸 docker-compose 单机特有产物**。我们 M0 单节点 DooD 才撞，M1+ 上编排层后 `/etc/hosts` 一手自然退役（与既定 K8s 方向一致，非永久债）。
3. **egress-proxy 静态 IP 是行业常态**：Cloudflare Outbound Workers（网络层注入凭证，同构 F-2）/ Blaxel（static-IP egress gateway 产品化）/ iron-proxy（proxy 自带 DNS）/ E2B（gateway VM IP tunneling）。

**落地**：
- **接缝（#592 已交付）**：`runtime_provider.extra_hosts`→`--add-host hostname:ip` argv（顺序保持）+ `HELIX_SANDBOX_EXTRA_HOSTS`（`name=ip,…`，格式错启动期 fail-closed 同 seccomp）+ supervisor app 接线（`make_sandbox_runtime_provider(extra_hosts=parsed_extra_hosts)`）。
- **本轮 CI 转正**：`test_supervisor_integration.py` egress 网络建固定 subnet（`172.30.0.0/24`）+ stub proxy 钉静态 IP（`172.30.0.10`）+ harness 传 `extra_hosts={credential-proxy.internal: 172.30.0.10}` + `_EGRESS_PROBE` 经**主机名** `credential-proxy.internal` 打 proxy。**runc + runsc 双 runtime 跑同一 `/etc/hosts` 路径**（比旧的 runc-only embedded-DNS 覆盖更强），**gate_49 由 `xfail` 转正**。
- **compose 落默认**：`helix-sandbox-egress` 加 `ipam` subnet + credential-proxy 钉 `ipv4_address: 172.30.0.10` + supervisor 注释给出 runsc 下应配的 `HELIX_SANDBOX_EXTRA_HOSTS` 具体值。dev/runc 仍留 unset（embedded DNS 正常）。
- **剩纯运维**：生产选不撞的私网段（避开 VPN/VPC/其他 docker 网络）+ runsc 环境配 `HELIX_SANDBOX_EXTRA_HOSTS`。无代码债。

#### 12.2.3 ③ Trivy 镜像 CVE 扫描（PR3，收尾）

实证修正：分镜像差异化门禁 + 全部 `--ignore-unfixed`，否则 office(LibreOffice) 卡 HIGH 恒红失效。

- **minimal 镜像补 CI 构建**：新 workflow `sandbox-image.yml`（对齐 office build pattern：buildx + gha cache + SHA-pinned actions），PR build+scan，推送目标随生产部署定。
- **分镜像门禁**（PR 阻断）：
  - minimal(alpine) / debian 基底：`--severity CRITICAL,HIGH --ignore-unfixed --exit-code 1`。
  - office(LibreOffice)：`--severity CRITICAL --ignore-unfixed --exit-code 1`（HIGH 降 weekly 报告——大依赖镜像卡 HIGH 恒红失效，且沙箱真隔离由 gVisor 提供，镜像内用户态库 CVE 优先级正当下调，理由入决策记录防质疑）。
- **豁免通道**：`.trivyignore`（带过期日期，强制复审禁永久 mute）入仓走 review。
- **周扫**：`schedule: cron`（weekly）对两镜像跑全量 Trivy（含 HIGH），fail → 开 issue / workflow 可见（基础镜像新爆 CVE 不等代码改动）。
- **SARIF 上传**：`github/codeql-action/upload-sarif` 进 Security tab 看趋势，不替代 exit-code 门禁。

> **实施注记（PR3 #579 + 修复 #580）**：
> - **门禁实现 = 双步 Trivy（report + gate）**。⚠️ **订正（#580）**：#579 最初的「单次 `format: sarif` + `severity` + `exit-code: 1`」是错的——trivy-action 在 `format: sarif` 下生成**全严重度** SARIF，且 `severity` 输入**不约束 exit-code**（[aquasecurity/trivy-action#95](https://github.com/aquasecurity/trivy-action/issues/95)），故单步门禁会对**所有**严重度判 fail，而非设计的 floor。实测：office `severity: CRITICAL` 门禁却被 HIGH 的 `pdfminer.six` 触发 fail。正解 = 两步：① **report**（`format: sarif` / `exit-code: 0`，全严重度，永不 fail）→ upload SARIF 进 Security tab；② **gate**（`format: table` / `severity` floor / `exit-code: 1`——table 格式下 `severity` 才真正约束 exit-code）。gate 带 `skip-db-update: true` 复用 report 已下的 DB。trivy-action `v0.36.0` / upload-sarif `v4.36.2` 均 SHA-pin。
> - **build context 修正（#580）**：minimal workflow build `context` 必须是 `infra/sandbox-image`（其 Dockerfile `COPY runner.py` 相对自身目录），#579 误抄 office 的 `context: infra`（office 是 `COPY sandbox-image/runner.py`）导致 minimal build 直接失败。两镜像 context 不同：office=`infra/` / minimal=`infra/sandbox-image`。
> - **「debian 基底」澄清**：实际只两镜像——minimal=alpine 卡 `CRITICAL,HIGH`，office=debian-slim+LibreOffice 卡 `CRITICAL`。设计原文「minimal/debian 卡 CRITICAL+HIGH」指「alpine minimal 与任何裸 debian 基底同档」，office 因 LibreOffice 用户态库面大单独降档，非第三个镜像。
> - **周扫不应用 `.trivyignore`（关键不变量）**：`sandbox-image-cve-weekly.yml` 故意省略 `trivyignores`，使被 PR 门禁 mute 的 CVE 仍现于周扫 SARIF/审计面——这正是 `.trivyignore` header 承诺的「mute 只对 PR 门禁生效、永不对审计面生效」的落地点，防止过期/遗忘的 mute 静默变盲。
> - **minimal 补 smoke test**：`infra/sandbox-image/smoke_test.py` + `smoke_payload.py`（stdlib-only，镜像 office smoke 驱动），验基底确实跑 Python 3.12 + baked runner.py 加载——build 不只「能构建」还「能运行」。
> - **退场说明**：「故意引入已知 CVE 包验 fail」未实现为常驻单测——在 CI 里常驻一个真漏洞包既是噪声又随 DB 更新漂移；门禁的正确性由 workflow 配置（severity/ignore-unfixed/exit-code/trivyignores 连线）+ 真扫描在真 base 上的行为保证。`.trivyignore` 当前无条目（纯纪律 header）。

### 12.3 边界（不做）

- **microVM（Firecracker/Kata）/ Sysbox**：M2/M3 升级路线。**Sysbox**（Daytona 用，唯一为「强隔离 + 保留 DooD 兼容」设计）作为 gVisor 的 DooD 友好对照候选记在册，对外开放不可信用户里程碑时与 Kata 一并评估（`02-sandbox-isolation.md` + 本 research）。HX-10 不改隔离范式。
- **gVisor 全局强制**：可配置（生产推荐 runsc / dev runc），不在代码里写死。
- **白名单极简 seccomp**：Python/glibc syscall 面大随版本变，误杀风险高维护贵；default+收紧档安全收益已足（调研3 + 决策）。
- **timing side-channel（Gate #6）CI 化**：环境敏感，CI runner 噪声大，留 staging。
- **prompt injection 本身的应用层防御**：lethal trifecta 切割（HX-2 反馈闭环 / approval gate 已部分覆盖外泄动作）属另一战线；HX-10 只管隔离爆炸半径，不假装解决注入根因。
- **运行时 seccomp 探测 / 动态 profile**：静态安全资产声明式入仓。

### 12.4 可观测

- misconfig + seccomp：无新增运行时指标——断言/校验是 build-time（测试红）+ 启动期（fail-closed），比指标更强。supervisor 启动日志记 seccomp profile 路径 + 加载结果 + 实际 oci_runtime。
- gVisor CI：workflow 成功/失败即信号；runtime 断言失败 = 明确测试失败。
- Trivy：CI exit-code 门禁 + weekly workflow 状态；CVE 列表进 job log / SARIF。

### 12.5 测试

- **PR1**：misconfig 断言（argv 无 socket/特权/cap-add/宿主 bind mount + 有 cap-drop/read-only/no-new-priv）；seccomp argv（None 无参数回归 / 非 None 含 `--security-opt seccomp=<path>` 位置正确）；profile JSON 解析断言高危 syscall 移出 allowlist（io_uring/userfaultfd/keyctl/bpf）**且 clone3 仍在 allowlist**（防误删回归）；supervisor 启动 fail-closed（配路径文件缺 → 拒启动）。
- **PR2**：集成测试 runtime env 参数化（默认 runc 回归）；runtime 真起断言（runc job 断言 runc / gvisor job 断言 runsc）；兼容坑断言（io_uring ENOSYS / proc-sys-net 不崩 / numpy 跑通）；CVE-2019-5736 PoC（runsc 挡 / runc skip）。gVisor workflow = 端到端验证。
- **PR3**：actionlint/yaml 验三 workflow 配置（minimal build+smoke+Trivy / office 加 Trivy 步 / weekly 矩阵）；分镜像 severity 差异（minimal `CRITICAL,HIGH` vs office `CRITICAL`）；周扫不带 `trivyignores`（审计面不被 mute）；minimal smoke test 真跑（build→docker run→OK）。**退场**：「故意引入 CVE 包验 fail」改为靠门禁配置正确性 + 真扫描行为保证（见 §12.2.3 实施注记）。

### 12.6 Mini-ADR

- **HX-K0 misconfig 断言是最高优先级、零成本防线**：实证前沿 LLM 对 docker-socket/特权/可写宿主挂载 100% 逃逸；config 属性非运行时开销；钉死防回归。fail-closed（断言红阻 merge）。
- **HX-K1 seccomp = 仓库自管 pinned profile（default 基线 + 显式收紧）**：隔离强度由代码版本决定不由宿主 Docker 版本；JSON diff 可审计。**实证修正 clone3 保留允许（禁了崩新 glibc）/ perf_event_open 移出强制 / io_uring 禁用但标兼容代价**。
- **HX-K2 安全配置事故 fail-closed（例外于 fail-open 公理）**：seccomp 配了加载不了 / misconfig 断言失败 = 配置错误非抖动，必须拒。语义边界入横切公理脚注。
- **HX-K3 gVisor 当期纳入（可配置 + CI 验证），不缓做**：实证消除缓做的成本顾虑（CPU-bound <3% / systrap 国内云可跑 / Tencent 百万级生产验证）；威胁模型修正——爆炸半径由跨租户决定不由信任决定，残差（hardened 下 40%-可利用内核 CVE 逃逸）影响跨租户，非平凡。生产推荐 runsc / dev runc / CI 持续验证。
- **HX-K4 runsc 二进制 OSS/ACR 转存分发**：国内生产主机直连 Google 源不可达；CI（海外）下载+校验后转存。systrap 平台无需 KVM/嵌套虚拟化，标准 ECS 可跑。seccomp 是内核特性无网络依赖。
- **HX-K5 Trivy 分镜像差异化 + `--ignore-unfixed` + 周扫**：实证修正——office(LibreOffice) 卡 HIGH 恒红失效，降 CRITICAL+周扫；minimal/debian 卡 CRITICAL+HIGH；全部 ignore-unfixed（门禁只卡可行动项）；沙箱真隔离由 gVisor 提供故镜像内用户态库 CVE 优先级正当下调。
- **HX-K6 集成测试 runtime env 参数化（默认 runc）**：单测试集两 runtime 复用，默认 runc 保本地/既有 job 零变更，gVisor job 切 runsc + runtime 真起断言防静默回落。
- **HX-K7 Sysbox 记为 DooD 友好对照候选**：实证发现 Daytona 用的 Sysbox 是唯一为「强隔离 + 保留 Docker 兼容」专门设计，比 Kata 更贴我们 DooD；对外开放里程碑时与 Kata 一并评估，不在 HX-10 选型。

### 12.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §12 + research 文档 + ITERATION-PLAN HX-10 细化 | 纯 docs，CI |
| PR1 | misconfig 断言 + seccomp pinned profile（删 clone3 收紧）+ runtime_provider `--security-opt` + settings + supervisor fail-closed | §12.5-PR1 |
| PR2 | gVisor CI workflow（runsc 装载 + runtime 真起断言 + 兼容坑/逃逸 PoC 用例）+ 集成测试 env 参数化 + environments 部署策略文档 | §12.5-PR2；workflow 端到端 |
| PR3（收尾，#579） | minimal 镜像 CI 构建（`sandbox-image.yml` build+smoke+scan）+ office 加 Trivy 步 + 两镜像分镜像门禁（minimal `CRITICAL,HIGH` / office `CRITICAL`，全 `--ignore-unfixed`）+ `.trivyignore` 纪律 header + `sandbox-image-cve-weekly.yml` 周扫（不应用 trivyignore）+ SARIF 上传 | §12.5-PR3；零债 6 条 |

## 13. HX-9 — 租户级出站 webhook hook（Wave 3 架构级）

> 方向级决策已对比讨论 + 拍板（2026-06-13），实证对比落 [research](../research/2026-06-13-tenant-hook-extension-patterns.md)。本节为拍板后的 STREAM 级详设。

### 13.1 问题 + 现状取证（2026-06-13，main@2871f91）

租户要在 agent 生命周期关键点挂自己的逻辑（run 完成通知内部系统 / 审批请求转发到自家 IM / 产物生成触发下游 ETL），今天只能轮询 API 或消费 SSE——**无平台主动推送的扩展点**。HX-9 = 平台在事件发生时签名 POST 到租户注册的 URL（webhook 回调式，Stripe/GitHub/Svix 范式）。

| 接缝 | 位置 | 复用方式 |
|---|---|---|
| manifest `hooks: dict[str,str]`（占位零消费方） | `protocol/agent_spec.py:811` | **转 deprecated**（注册改 API CRUD，见 ①） |
| triggers CRUD 全套（J.10） | `api/triggers.py` / `persistence/trigger/{base,memory,sql}.py` / `models/agent_trigger.py` | webhook_endpoint CRUD/Store/ORM 模板 |
| 3 个事件源表 | `agent_run`（终态）/ `agent_approval` / `artifact` | worker 直读（方案 b，见 §13.2.2）——各带 tenant_id/时间戳/payload |
| DLQ worker + 退避表 | `memory/dlq_worker.py:76`（`_BACKOFF_SCHEDULE`/`_MAX_ATTEMPTS`）/ `feedback_consumer.py:104` | 投递 worker 形态 |
| SSRF | `helix-common/url_validation.py:44` / `api/mcp_servers.py:52` host-pivot 字符黑名单 | URL 注册双段校验 |
| RLS bypass 跨租户扫 | `persistence/rls.py:92` `bypass_rls_var` | worker cross-tenant 扫描 |
| audit + token_usage 计量 | G.9 / Stream K | 投递可记账可审计 |

### 13.2 设计

**架构（worker 读 3 源表 → 投递）**：

```
事件源 = 三张现成表（各带 tenant_id / 时间戳 / payload，零 orchestrator 改动）
  ├─ run.completed / run.failed ← agent_run（终态 status + finished_at）
  ├─ approval.requested        ← agent_approval（新审批行）
  └─ artifact.saved            ← artifact（新产出版本）
        │
        ▼
WebhookDeliveryWorker（control-plane lifespan，照 MemoryDLQWorker）
  cross-tenant 扫 3 源表新行（per-source 游标，仅作扫描量优化）
  → 匹配租户已注册 endpoint 的 event_type
  → 入 webhook_delivery 队列表；UNIQUE(endpoint_id, event_id) 幂等去重
    （event_id = run:{run_id} / approval:{id} / artifact:{ver_id}）
  → SSRF 双段校验 + HMAC-SHA256 签名 → POST 租户 URL
  → 2xx=delivered / 5xx·timeout=指数退避 / 4xx=不重试直接 dead_letter
  → per-endpoint 断路器 + per-tenant 并发上限
```

#### 13.2.1 ① 注册面 = 平台 API CRUD（PR2）

hook URL 是**运维配置资产**非 agent 行为——改 URL 不该弹 agent 版本。照 triggers 模板：`webhook_endpoint` 表（`tenant_id` + `name`（tenant 内唯一）+ `url` + `event_types`（订阅哪几类）+ `agent_name`（可空，NULL=全 agent）+ `secret_hash` + `enabled` + `source`）；5 端点 CRUD；HMAC secret show-once（创建响应返一次明文，存 hash）；per-tenant endpoint 配额。manifest `hooks` 字段标 deprecated（docstring + 不接线），未来若要 manifest 引用走「转引用 endpoint id」而非内联 URL。

#### 13.2.2 ② 起步事件集 = 三类 via 方案 (b)（worker 读 3 源表）

`run.completed` / `run.failed`（agent_run 终态）+ `approval.requested`（agent_approval）+ `artifact.saved`（artifact）。

**取数机制订正（2026-06-13）**：原 §13 PR0 写「方案 (a) 补 run_event emit 帧」，实现期深读发现该假设站不住——run_event 主轴只有 `metadata/updates/retry/error/end`，**没有干净的 run 终态帧**（终态在 agent_run.status），且 **run_event 表无 tenant_id 列**（靠 run_id join agent_run 走 RLS）。故 (a) 实际要补 **4** 个 emit 帧（动 orchestrator 热路径 4 处 + 污染终端用户 SSE 流），且 worker 跨租户扫 run_event 拿投递路由 tenant_id 仍须 join agent_run——「单一数据源」不成立。

改 **方案 (b)**：worker 直接扫三张源表（agent_run 终态 / agent_approval / artifact），各表本就带 tenant_id + 时间戳 + payload 字段，**零 orchestrator 改动、无 SSE 污染、零 join**。代价仅 worker 3 个 per-source 游标——而 `webhook_delivery` 的 `UNIQUE(endpoint_id, event_id)` 已保证幂等投递，**游标只为省扫描量、非正确性关键**（粗粒度时间窗重扫也不会重复投）。三类事件**范围不变**，仅取数从合成主轴换成读自然源表。详见 Mini-ADR HX-J1。

#### 13.2.3 ③ 投递基建 = 自建薄版（PR3）

`WebhookDeliveryWorker` 照 `MemoryDLQWorker`/`FeedbackConsumerWorker` 形态（`start/stop/run_once` + lifespan flag + 优雅 stop）。`webhook_delivery` 队列表（照 `trigger_run`/`memory_writeback_dlq`：`status`(pending/delivered/failed/retrying/dead_letter) + `attempt` + `next_retry_at` + `error` + 部分索引）。指数退避 `(60s,5m,30m,2h,6h)` + max 5 attempts + DLQ；**4xx 不重试**（配置错误非瞬态）；per-endpoint 断路器（连续失败熔断，慢端点不反压邻租户）；per-tenant 并发上限。不引 Svix（为通用平台设计、复杂度过剩，引第三方与「多租户数据不出平台」冲突，事件集窄 PG 队列表足够）。

#### 13.2.4 ④ 仅出站通知（边界）

同步阻塞改写回调（pre-run 校验等）把租户端点拉进 run 关键路径 = 延迟 + 可用性耦合，违 fail-open 公理——**不做**，改写类需求归 M1-F 中间件评审。HX-9 纯出站异步通知。

**事件 payload**：`{event_id, event_type, occurred_at, tenant_id, seq, payload}`；at-least-once，消费方按 `(tenant_id, seq)` 幂等。**签名**：HMAC-SHA256 per-endpoint secret（header `X-Helix-Signature-256`），**请求绝不携带平台凭证**。**SSRF**：注册时 `validate_remote_url`（私网/metadata 阻断）+ host-pivot 字符黑名单；投递前解析后 IP 再校验（防 DNS rebinding）。

### 13.3 边界（不做）

- 进程内租户代码（middleware/plugin 模式 B/C）——边界「非任意代码」，M1-F2 另有归属。
- 同步阻塞改写回调（④）——可用性耦合违 fail-open，归 M1-F。
- 消息中间件（Kafka 等）——per-run 个位数事件量，PG 队列表足够，M2 再议。

### 13.4 可观测

投递成功率 / DLQ 深度 / per-endpoint 断路器状态 / 投递延迟指标；投递次数+失败率进 token_usage 同款计量面（chargeback 可定价，[memory:billing-meter]）。

### 13.5 测试

- **PR1**：Store 三层 round-trip（endpoint + delivery）+ 租户隔离（get/list/update/delete 跨租户返空）+ 唯一约束（endpoint name / delivery dedup）+ list_ready 状态过滤 + 配额 count；迁移真 PG（RLS + 部分索引）。**纯数据模型，零 orchestrator 改动（方案 b）。**
- **PR2**：5 端点 CRUD + tenant scope + 跨租户（Stream N）+ audit emit + secret show-once + 配额 429；SSRF 拒私网/host-pivot；admin-ui vitest + Playwright（CI）。
- **PR3**：worker 退避/DLQ/断路器状态机；HMAC 签名正确性 + 验签；4xx 不重试 / 5xx 退避 / 耗尽 DLQ；per-tenant 并发；e2e（注册→触发三类事件→stub 收签名 POST + 幂等去重）。

### 13.6 Mini-ADR

- **HX-J0 注册面 = API CRUD 非 manifest**：hook URL 是运维配置非 agent 行为，改 URL 不弹版本；triggers 已证 API 路线治理面够；manifest `hooks` 转 deprecated。
- **HX-J1 worker 读 3 源表（方案 b），非补 run_event emit 帧（方案 a，已否决）**：实现期发现 run_event 无干净 run 终态帧 + 无 tenant_id 列（join agent_run 才有），(a) 实际要补 4 帧 + 污染终端 SSE 流且仍须 join。改 worker 直读 agent_run/agent_approval/artifact 三源表（零 orchestrator 改动 / 无 SSE 污染 / 零 join）。`webhook_delivery` 的 `UNIQUE(endpoint_id, event_id)` 幂等去重使 per-source 游标仅为扫描量优化、非正确性关键。诚实记：三类事件取数是 worker 3 游标的内部复杂度，不外溢。
- **HX-J2 自建薄版非 Svix**：三套 worker 模板同构，Svix 复杂度过剩 + 引第三方违数据不出平台；事件集窄 PG 队列足够。
- **HX-J3 仅出站异步（fail-open 公理）**：投递故障绝不影响 run；同步改写回调拉租户端点进关键路径 = 可用性耦合，归 M1-F。
- **HX-J4 爆炸半径由跨租户决定（同 HX-10 公理）**：per-tenant 队列 + per-endpoint 断路器，慢/坏端点不反压邻租户。
- **HX-J5 hook 请求不带平台凭证**：纯通知 + HMAC 签名；SSRF 双段校验（注册时 + 解析后 IP）防 rebinding。

### 13.7 PR 切分

| PR | 内容 | 验证 |
|----|------|------|
| PR0（本设计） | §13 + research 文档 + ITERATION-PLAN HX-9 细化 | 纯 docs，CI |
| PR1 | 迁移 `webhook_endpoint`+`webhook_delivery` 两表 + RLS + 部分索引 + DTO/ORM/Store 三层 + ResourceType 双镜像（AuditAction 推迟到 PR2 emit）。**纯数据模型，零 orchestrator 改动（方案 b）** | §13.5-PR1；迁移真 PG |
| PR2 | `api/webhook_endpoints.py` 5 端点 CRUD + authz + 跨租户 + AuditAction WEBHOOK_* + secret show-once + 配额 + SSRF + admin-ui（SDK/页面/tab/i18n/接线） | §13.5-PR2 |
| PR3a | 投递引擎：`WebhookDeliveryWorker` 消费 `webhook_delivery`（`list_ready`→SSRF 复检+HMAC-SHA256 签名 POST→2xx/4xx/5xx 状态机+退避/DLQ+per-endpoint 断路器+per-tenant 并发）+ lifespan 接线 + 计量 + 可观测 | §13.5-PR3；自足于 webhook_delivery store |
| PR3b（收尾） | 入队扫描：`enqueue_once` 扫 3 源表（**复用现成 `list_all_tenants`，零新增 store 方法**）→ agent 范围经 thread_meta 反解 → 幂等入队 → 端到端闭环 | §13.5-PR3；零债 6 条 |

> **PR3 拆分 + PR3b 实现注记（2026-06-13）**：投递引擎（PR3a，自足于 webhook_delivery store）与入队扫描（PR3b）拆两 PR，各自独立 review+CI 绿。**PR3b 复用 3 源表现成 `list_all_tenants`（run/approval/artifact），不新增 list-since 方法**——`UNIQUE(endpoint_id,event_id)` + `exists_for_event` 预检使 bounded 窗口重扫（scan_limit=500/源/cycle）幂等安全，故游标精度只影响扫描量非正确性。agent 范围匹配经 `thread_meta.get(thread_id)` 反解（run/approval 携 thread_id）；**artifact 父行无 thread → artifact.saved 仅匹配 all-agents endpoint（agent_name=None），文档化 M0 限制非静默丢**（artifacts 本是 user-scoped 资源）。PR3a 单独 merge 时无人入队（非端到端），PR3b 闭环。
