# PI-2b — Model-backed 输出 judge 档（架构设计）

> 日期：2026-06-15
> 触发：PI-2a live 红队（真 deepseek，重建 PI-1/1b/2 栈）实测 —— `injection-004-image-exfil`
> 被 PI-2 规则层确定性拦下（`[SAFE screen-blocked]`），但 `injection-001/002/003`
> **裸 token 内联注入仍 3/3 泄漏**。规则层结构上 shape-match 不到任意 canary → 需 model-backed judge。
> 上游接 [`2026-06-15-prompt-injection-defense-design.md`](./2026-06-15-prompt-injection-defense-design.md) §3.2「输出筛查…重则接 judge」。

---

## 1. 定位与命名

- **PI-2b = PI-2 输出筛查的 judge 升级档**（model-backed 输出判定）。是 PI-2 规则层之上的**第二级**，
  不是上游文档 §6 表里的 `PI-3`（那行是**输入分类器 + 动作筛查**，独立后续）。
- 规则层（PI-2）确定性、零成本、抓 **shape-matchable** 泄漏（凭证形态 / 图片 exfil）。judge 档抓规则层
  的盲区：**任意 token 泄漏 + 语义层注入失准**（用户没要、响应却照内嵌指令做）。

## 2. 核心洞察（堵真 gap 的关键）

裸 canary（`CANARY-7F3A21`）规则抓不到，因为它**无固定形态**。但 judge **不需要知道 canary**：
按 LlamaFirewall **AlignmentCheck**（arXiv 2505.03574）思路 —— 判**响应是否被注入意图带偏**，而非匹配具体密文。

- injection-001：用户请求 = 「总结工单」；响应 = 吐一个随机 token `CANARY-7F3A21`。
  judge 看 **(用户原始请求, 响应)** → 响应做了用户没要求的事（复述无关 token）= **misaligned** → 拦。
- 这把「检测泄漏」从「匹配密文」转成「**检测响应-请求失准**」，无需任何 canary 先验。

## 3. 判定接口（CI 接缝)

judge 调用藏在 protocol 后，CI 用 fake judge 确定性测（**无 model key**，沿用全仓「设计藏接缝」惯例）。

```python
# expert-work-protocol
class OutputJudgeVerdict(BaseModel):
    aligned: bool                      # 响应是否对齐用户原始请求(未被注入带偏)
    leak_suspected: bool               # 是否疑似泄漏机密/越权
    reason: str                        # 简短理由(类别级,不回填密文)

class OutputJudge(Protocol):
    async def judge(
        self, *, user_request: str, response: str, context_hint: str | None
    ) -> OutputJudgeVerdict: ...
```

- `user_request` = 本轮用户消息（可信通道，judge 的对齐基准）。
- `response` = 模型最终输出。
- `context_hint` = 可选，告诉 judge「系统上下文含机密，不得外吐」的**类别提示**（非密文本身）。
- 返回 `aligned=False or leak_suspected=True` → 拦（换 PI-2 的 `REFUSAL_TEXT`）。

**判定实现** = LLM-as-judge，few-shot rubric（注入/泄漏正反例）。判定模型经平台凭证 `resolve_provider`
解析（系统级，复用 embedder/rerank 那条路径，**非** agent 主模型 manifest key）。

## 4. 接线点与门控

- **运行位置**：`agent_node` 拿到 `response` 后，PI-2 规则层**之后**。规则已拦 → 短路不调 judge（省成本）。
- **只在 terminal 响应跑**（无 tool_calls 的最终回复）：judge 是 per-response 一次 LLM 调用，
  中间 tool-call 步不跑，控成本/延迟。
- **门控（默认关）**：`DefenseSpec.output_judge: Literal["block", "off"] = "off"`。
  judge 有成本/延迟 → 默认关，高敏 agent 显式开。规则层（`output_screen`）保持默认开。

## 5. 降级语义（必须显式拍板的安全取舍)

judge 是 LLM 调用 → 会超时 / 限流 / 不可用。两种失败语义：

| 模式 | 行为 | 适用 |
|---|---|---|
| **fail-open**（默认） | judge 失败 → 放行 + metric + warn 日志 | judge 是规则层之上的**尽力兜底**;失败不该让正常响应全挂 |
| **fail-closed** | judge 失败 → 当作不通过,拦 | 高敏 agent;可配 `output_judge_on_error: "open"\|"closed"` |

默认 **fail-open**：judge 是 backstop，不是主门禁；judge 抖动就拦掉所有响应会严重伤可用性。
高敏场景显式切 fail-closed。**超时**单设 budget（如 5s），超时按降级语义处理。

## 6. 可观测

- `expert_work_output_judge_total{verdict}`（aligned/misaligned/leak/error）—— 计数 + 失败率。
- `expert_work_output_judge_latency_seconds` —— judge 调用延迟（成本/延迟 SLO）。
- 拦截走 PI-2 既有 `_output_screen_blocked_total{category="judge"}` 复用，或单列。
- 日志只记 verdict + reason 类别，**绝不回填响应密文 / canary**。

## 7. 验收

- **CI（无 key）**：fake judge（scripted verdict）驱动 graph，确定性证：judge=misaligned → 响应被换 refusal；
  judge=aligned → 放行；judge 抛错 → 按 fail-open/closed 配置走。
- **离线**：`adversarial.py` 加 judge-responder 变体，injection-001/002/003 经 judge → 应翻 SAFE。
- **live**：真 judge 模型跑 `verify_live.py` → **injection 三条裸 canary 翻 `[SAFE judge-blocked]`**，safe_rate→1.0。
- 指标：注入 ASR 周跑入库（接 S2.1 eval worker）。

## 8. 不做 / 边界

- **不审完整 CoT**：AlignmentCheck 原版审推理链需 reasoning-trace 接口（上游 §5 暂记）;PI-2b 只用
  (user_request, response[, context_hint]) 黑盒判定,够堵输出泄漏,不引 trace 依赖。
- **不自训分类器**：judge = 通用 LLM + rubric;PromptGuard2/Llama Guard 自托管输入分类是 PI-3 输入档的事。
- **Llama Guard 不复用为本 judge**：其 taxonomy 是危害类(暴力/仇恨),非「泄漏机密/注入失准」,方向不符。

## 9. PR 切分

| PR | 内容 | model 依赖 |
|---|---|---|
| **PI-2b-1** | protocol `OutputJudge`/`OutputJudgeVerdict` + agent_node 接线 + 门控 + 降级 + fake-judge CI 测 | 无(fake) |
| **PI-2b-2** | LLM-as-judge 实现(few-shot rubric)+ 平台凭证解析 + 离线 adversarial judge-responder | 有 |
| **PI-2b-3** | live 验证(`verify_live` judge 档)+ 指标入库 | 有(手动) |

- 分支 `stream-pi/pi2b-*`，footer `Co-authored-by: leyi`。

## 10. 引用

- LlamaFirewall（AlignmentCheck）arXiv 2505.03574 · [scanner 文档](https://meta-llama.github.io/PurpleLlama/LlamaFirewall/docs/documentation/scanners/alignment-check)
- Llama Guard arXiv 2312.06674（对比:危害分类,非本 judge 方向）
- 上游 PI 设计 `2026-06-15-prompt-injection-defense-design.md` §3.2 / §5
