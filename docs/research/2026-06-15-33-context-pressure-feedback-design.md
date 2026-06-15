# 3.3 Context-Pressure 反馈设计（★3→★5）

> T1 起点（用户拍板）。让 agent **知道**自己的 context 预算压力，据此行为收敛——
> 补足现有「静默裁剪/压缩」级联的盲点：裁剪发生但模型看不到「我快到上限了」。

## 1. 现状（勘探结论）

token 估算 + context 管理级联**已齐全**，但全是**模型不可见的内部裁剪**：

| 层 | 文件 | 触发 | 模型可见? |
|---|---|---|---|
| WorkingWindow（CM-2，默认 ON） | `context/working_window.py` | `context_window*threshold_pct`(0.7) | ❌ 静默裁 |
| ContextCompressor（L.L2） | `context/compressor.py` | 同上(0.7) | ❌ 静默压缩 |
| DynamicContextMiddleware（E.3，opt-in） | `runtime/middleware/dynamic_context.py` | `max_tokens` cap | ❌ 静默裁 |
| token estimator（HX-1） | `runtime/tokens.py:default_estimator` tiktoken | —— | —— |
| `_resolved_context_window(model)->int` | `agent_factory.py:1164`（manifest>catalog>200K） | —— | —— |

**真 gap 一句话**：token 计数 + 裁剪能力全在，但**压力信号从不写进给模型的提示**——
agent 不知道自己还剩多少预算，无法主动收敛（快收尾 / 别开新支线）。缺的是**注入层**，非计数能力。

## 2. 方案

新 `ContextPressureMiddleware`（`helix-runtime/middleware/context_pressure.py`，与 dynamic_context 同层）：

- **anchor** `before_llm_call`，**after** `dynamic_context`（在裁剪之后量「真正要发的 prompt」）。
- **度量**：`prompt_tokens = Σ estimator(flatten_message(m))` over `payload["messages"]`；
  `pressure = prompt_tokens / context_window`（分母 = `_resolved_context_window`，恒为 int）。
- **注入**：`pressure >= warn_pct`（默认 0.75）时，向**末条消息**尾部追加一段模型可见提示：
  ```
  [Context budget: ~{remaining} of {window} tokens left ({used}% used). You are nearing the
  context limit — prioritise summarising progress and concluding over starting new lines of work.]
  ```
  低于阈值 → 原样透传（静默）。

## 3. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| **注入位置** | 末条消息尾部追加（非 system prompt 头部） | 保 Anthropic 前缀缓存（leading system 不动）；模型读最新上下文 |
| **分母** | `_resolved_context_window`（模型真上限） | post-trim vs `max_tokens` cap 会恒满无意义；vs 模型窗口才反映真压力 |
| **量测时机** | 级联之后（post working_window/compressor/trim） | 量「真正发出的 prompt」；级联压下去了就不报（它在处理），压不下去（compressor max_passes 用尽仍重）才报=真该收敛 |
| **默认 ON** | 是（阈值门控，正常会话静默） | 同 WorkingWindow 先例（默认开的 context 管理）；opt-in 则多数 agent 永不受益，违「能力护城河」意图；manifest 可关 |
| **阈值** | 默认 0.75（略高于压缩 0.7 触发） | 压缩目标 0.7；warn 在压缩仍压不下时报=真实卡在上限附近，信号稀有有意义 |

## 4. 接线

- `agent_spec.py` `ContextCompressionPolicy` 加两字段：
  `pressure_feedback: bool = True` + `pressure_warn_pct: float = Field(0.75, gt=0, le=1)`。
- `middleware_assembly.build_middleware_chains` 收 `context_window: int` 参数（agent_factory:436 传
  `_resolved_context_window(spec.spec.model)`）；默认建 `ContextPressureMiddleware`（`pressure_feedback` 为 False 才跳过）。
- estimator 复用同一 seam（与 dynamic_context / token_usage 同基）。

## 5. 测试

- 单测 `test_context_pressure_middleware.py`：
  - 低于阈值 → 不注入（消息不变）；达/超阈值 → 末条尾部出现提示。
  - 前缀不变（leading messages 逐条 identical，只末条变）。
  - 空消息 / 单 system → 透传不炸。
  - remaining/used% 数字正确。
  - estimator 注入生效。
- 集成 `test_middleware_assembly` / chain：pressure_feedback 默认建、False 跳过、阈值传递。

## 6. 不做

- 不动现有级联（working_window/compressor/trim 行为不变）。
- 不注入 step 压力（"第 N/M 步"）—— 另一信号，本项专注 token 压力（计划命名）。
- 不改 system prompt 头部（前缀缓存）。
