# Stream HX — Harness 强化（设计先行）

> **背景**：2026-06-10 完成 helix harness 十维能力评估（`docs/research/2026-06-10-helix-harness-capability-assessment.md`），用户拍板把全部非强项做强，按 Wave 严格顺序推进（Wave 1 零债收完再进 Wave 2）。每条子项设计先行 + 零债收尾（Stream CM 同节奏）。
>
> **设计先行规则**（[memory:design-first-iteration]）：所有总体架构 / 跨切面接口 / Mini-ADR 在本文件锁定；每条子项 PR 在对应章节基础上做局部细化。
>
> **零债收尾规则**（[memory:zero-tech-debt]）：每条交付收尾 6 条全过 —— 无 TODO / 测试达标 / 文档同步 / 可观测齐全 / CI 全绿 / bug 不遗留。
>
> **横切公理**（HX-12/13 立项时锁定，对全 Stream 生效）：① 不存在 drop core tool / drop 历史真相的代码路径（视图级裁剪 ≠ 状态删除）；② fail-open——基础设施故障的代价只能是多花 token，绝不能是少能力；③ config 防御解析（clamp / safe default，不 raise）。
>
> **本文件状态**：HX-1（真 tokenizer + 长上下文阈值参数化，§2）详设已锁定。HX-2/3/4 与 Wave 2 各条开工时追加章节。

---

## 1. 范围 & 边界

| ID | 评估维度 | Gap | 交付 | 详设 |
|----|---------|-----|------|------|
| **HX-1** | ③ 上下文工程 | `len//4` 估算漂移（CJK 严重低估）+ `context_window` 不随目录解析 + E.3 遗留 8K 默认裁剪 | TokenEstimator 协议 + tiktoken 默认实现 + 目录解析 + 遗留默认值退役 | §2（本文） |
| HX-2 | ⑦ 学习闭环 | 用户反馈→学习断链 | feedback consumer worker | 开工时追加 |
| HX-3 | ⑧ 容错 | run 级瞬态故障无自动重试 | 瞬态分类 ∧ 零 irreversible 调用 → 重试 1 次 | 开工时追加 |
| HX-4 | ⑨ 可观测 | 工具延迟/成功率/approval 队列指标缺 | 直方图 + counter + gauge + 日志字段统一 | 开工时追加 |
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
