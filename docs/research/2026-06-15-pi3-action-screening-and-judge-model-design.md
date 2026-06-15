# PI-3b 动作筛查 + 平台级 judge 专用模型（架构设计）

> 日期：2026-06-15
> 触发：Stream PI 输出防御收官（safe_rate 1.0），但 **1.0 是在 `tools=0` 的红队集上拿的**。
> PI-2/2b 只筛**最终文本响应**，**不筛 tool-call 参数** → 用工具的 agent（目标产品形态 per-user
> 持久 agent + MCP 工具）有**注入诱导越权工具调用**的未覆盖面。
> 上游：[`2026-06-15-pi2b-output-judge-design.md`](./2026-06-15-pi2b-output-judge-design.md)。

两件共享 judge-model 基础，合并设计：
- **(A) 平台级 judge 专用模型配置**：镜像 embedder 平台配置；不配则复用 agent 自己的模型。
- **(B) PI-3b 动作筛查**：工具派发前判 (用户请求, 工具调用) 对齐，注入诱导的越权调用拦/送审。

---

## A. 平台级 judge 专用模型配置

### A.1 动机

judge 现复用 agent 自己的主模型（`runtime._make_output_judge`，PI-2b-3）。问题：
- 主模型可能贵/慢；judge 是 per-response（PI-3b 后 per-tool-call）高频调用 → 专用便宜模型省成本。
- 平台运维想统一指定一个 judge 模型，而非每 agent 各自。

### A.2 方案（镜像 embedder 平台配置，全栈已勘）

完全照搬 `platform_embedding_config` 这条栈，judge 版：

| 层 | embedder 现状（参照） | judge 新增 |
|---|---|---|
| DB 表 | `platform_embedding_config`(singleton,无 RLS,bypass session) | `platform_judge_config`(同型;`judge_provider`/`judge_model`/`updated_*`) |
| Persistence | `persistence/platform_embedding_config/{base,sql}.py` | `platform_judge_config/{base,sql}.py` |
| Service | `PlatformEmbeddingConfigService.effective_embedding_config()→(prov,model)|None` | `PlatformJudgeConfigService.effective_judge_config()→(prov,model)|None` |
| API | `GET/PUT /v1/platform/embedding-config`(system_admin) | `GET/PUT /v1/platform/judge-config`(system_admin) |
| 校验 | provider 在平台凭证内 + model 有 embeddings 能力 | provider 在平台凭证内（judge 是通用 chat，不需特殊能力位） |
| runtime | `resolve_embedder`(DB→env 降级) | `_make_output_judge` 改：DB judge config→agent 自己模型降级 |
| admin-ui | `PlatformEmbeddingSection.tsx` | `PlatformJudgeSection.tsx`（同页 SettingsPlatformConfig） |

### A.3 降级语义（用户拍板）

**有平台 judge config → 用它；没有 → 复用 agent 自己的主模型**（PI-2b-3 现行为，不破坏）。
judge config 是**可选**（不像 embedder 配置门控长期记忆）；缺失优雅降级，非报错。

### A.4 admin-ui 友好提示（硬要求）

「很多人不知道 judge 专有模型干啥」→ 区块顶部说明文案 + 字段 tooltip：

> **输出审查模型（Judge）**——用一个独立模型审查 agent 的回复/工具调用是否被 prompt 注入劫持或泄露机密。
> 建议选一个**便宜、快**的模型（它高频调用）。**留空则复用每个 agent 自己的主模型**（更准但更贵/慢）。
> 仅当 agent 在 manifest 里开启 `defenses.output_judge`/`action_screen` 时才生效。

未配置态显示「未配置——judge 将复用各 agent 自己的模型」(info，非 warning，因为这是合法降级)。

### A.5 judge config 服务双判官共享

同一 judge 模型同时供 **输出 judge(PI-2b)** + **动作 judge(PI-3b)**。`_make_output_judge` 与新
`_make_action_judge` 都先查平台 judge config、降级到 agent 模型，构建各自 judge（同 `LLMCaller`）。

---

## B. PI-3b 动作筛查

### B.1 威胁与现状

注入诱导 agent 调用用户没要求的工具（经 http 工具外泄数据 / 删东西）。PI-2/2b 看不到 tool-call args
（PI-2b judge 仅在**无 tool_calls 的 terminal 响应**跑——`builder.py:607` 显式 `not _extract_tool_calls`）。
→ tool-using agent 的动作注入面**当前零防御**。

### B.2 方案：工具派发前的对齐判定（复用 OutputJudge 架构 + approval gate）

- **`ActionJudge` protocol**（镜像 `OutputJudge`）：
  ```python
  @dataclass(frozen=True)
  class ActionVerdict:
      aligned: bool   # 工具调用是否服务用户实际请求(非注入诱导)
      reason: str
      @property
      def blocked(self) -> bool: return not self.aligned

  class ActionJudge(Protocol):
      async def judge(self, *, user_request: str, tool_name: str,
                      tool_args: Mapping[str, Any]) -> ActionVerdict: ...
  ```
  实现 `LLMActionJudge`(few-shot rubric:用户请求 vs 工具调用对齐)+ `FakeActionJudge`(CI)。

- **接线点**：`tools_node`（`builder.py:671`），approval-gate 检测**之前**，对每个 tool_call 判对齐。
  这里能拿 `state["messages"]`(→`_latest_human_text` 取 user_request)+ tool_calls。比 middleware 路径
  干净（middleware 拿不到 messages，勘察方案 B 要塞 ToolContext，绕）。

- **门控**：`DefenseSpec.action_screen: Literal["off","block","approval"] = "off"`（默认关，per-tool-call
  LLM 调用有成本；opt-in）。
  - `"block"`：不对齐 → 拒该工具(error ToolMessage + recovery advisory，agent 下轮自修正)。复用现有
    中间件拦截路径(`TOOL_BLOCKED` audit + `AuditResult.DENIED`)。
  - `"approval"`：不对齐 → 送 **approval gate**(写 `pending_approval`，复用 J.8 暂停/resume/人审机制)。
    人批准则照常派发、拒绝则 `approval_outcome="rejected"`。无人值守则 `approval_timeout_s` 后超时=拦。

- **降级**：judge 失败 → `action_screen_on_error: Literal["open","closed"]="open"`（同 PI-2b，尽力兜底）。

- **只对真要派发的 tool_calls 跑**：approval gate 已豁免的不重复；judge 失败/超时按降级。

### B.3 可观测

`helix_action_screen_total{verdict}`(aligned/misaligned/error)。拦截复用 `_emit_tool_audit`
(`action=TOOL_BLOCKED, reason="action_screen_misaligned"`)。日志只记 verdict+reason 类别,不回填 args。

### B.4 验收

- CI(无 key):fake ActionJudge 驱动 graph——misaligned+block→工具被拒/agent 收 error;misaligned+
  approval→pending_approval;aligned→正常派发;judge 抛错→fail-open/closed。
- live:给 redteam agent 加一个 http 出站工具 + 注入「把答案 POST 到 attacker」用例,验 action screen 拦。
  （需扩对抗集加 tool-using injection 用例。）

---

## C. 分期与 PR 切分

| PR | 内容 | model 依赖 |
|---|---|---|
| **PI-3-A1** | DB 表 + persistence + `PlatformJudgeConfigService` + GET/PUT API + 单测 | 无 |
| **PI-3-A2** | `runtime._make_output_judge` 改用平台 judge config（降级 agent 模型）+ 单测 | 无(fake) |
| **PI-3-A3** | admin-ui `PlatformJudgeSection`(友好提示)+ SDK + i18n 双语 + story + 测试 + SE-8 接线 | 无 |
| **PI-3b-1** | `ActionJudge` protocol + `LLMActionJudge`/`FakeActionJudge` + tools_node 接线 + 门控 + 降级 + fake-CI | 无(fake) |
| **PI-3b-2** | `_make_action_judge`(共享 judge config)接线 + 对抗集加 tool-using 用例 + live 验证 | 有(手动) |

- 分支 `stream-pi/pi3-*`，footer `Co-authored-by: leyi`。
- 顺序:A1→A2→A3(judge 模型配置基础先就位,便宜模型对高频 action screen 收益最大)→PI-3b-1→PI-3b-2。

## D. 引用

- LlamaFirewall AlignmentCheck（动作/推理对齐）arXiv 2505.03574
- 上游 PI-2b 设计 + embedder 平台配置栈（`platform_embedding_config.py` 等，已勘）
