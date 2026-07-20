# B3 成本/token 熔断 + dynamic_worker 平台配置节 —— 设计

> 日期:2026-07-20。Backlog P1(docs/BACKLOG.md)。交付:2 PR(PR1 熔断全链,PR2 平台配置节)。

## 背景

运行护栏三件套(步数 `max_iterations` / 时长 `run_deadline_s` / 成本)缺成本这条——失控 run 只能靠步数或时长兜,token 烧穿无独立闸。典型逃逸:大上下文 agent 每步 100k input,30 步 = 3M token,步数闸与时钟闸都不响。

### 现状盘点(2026-07-20 溯源核实,file:line 见研究记录)

- `token_usage` 表**无 run_id 无成本列**(per-run 汇总靠 trace_id join,`token_usage_store.py:34`);**运行中无任何 token 累计器**(AgentState 零 token 字段)。
- 计量点:`TokenUsageMiddleware`(after_llm_call)只挂主 agent_node(`middleware_assembly.py:145`);**辅助调用(planner/reflect/压缩/记忆/judge)完全不计量**;cache hit 仍落 token_usage 行(after-chain 无条件跑,`builder.py:948`)。
- 钱只在月度离线 rollup 算(`billing-rollup-job/job.py:295`,`model_rate_card` 单价表);运行中无实时成本。
- **优雅收尾先例现成**:max_steps/max_no_progress 超限→`budget_exhausted`(`builder.py:528`)→`tools=[]` + 收尾指令一轮总结(`builder.py:785-789`),run 正常完成;`MaxStepsExceededError` 实际已无人 raise(死路径)。
- **预警先例现成**:75% 步数 `budget_signal`(`builder.py:745`);ContextPressure 预算注通道(附最后一条消息、不碰 system 前缀保 prompt cache,`middleware_assembly.py:204`)。
- **共享对象下传先例现成**:`deadline_at` 经 `_child_config` 原样下传子树(`_child_run.py:456`)。
- dynamic_worker 三参数(`max_concurrent`/`max_per_run`/`max_iterations`,`settings.py:593-606`)纯 env,零 API/UI;平台配置节先例 = `platform_tool_budget_config` 全套(model+store+service+GET/PUT+UI 节,DB-wins-over-env)。
- 顺带发现记 backlog(不在本 epic 修):①`run_deadline_s` 只在委托边界检查,纯主循环 run 超时不断(`subagent.py:141`/`spawn_worker.py:170` 之外无检查点)②aux 调用计量缺失。

## 决策纪要(用户 2026-07-20 拍板)

1. **口径 = token**(非成本):`input + output + cache_creation + cache_read` 四项合计,与 token_usage 行同源;cache hit 同计(与现行计量行为一致)。钱的闸留未来。
2. **超限行为 = 优雅收尾 + 80% 预警**:复用 max_steps 收尾机制;跨 80% 后每步注预算提示引导模型收敛。
3. **机制 = 共享 TokenBudget 对象**:每步零成本累计,deadline_at 同款下传,**主 + 静态 subagent + 动态 worker + 孙 worker 共扣一个池**。
4. **small 搭车 = dynamic_worker 三参数全暴露**:照 platform_tool_budget_config 先例建平台配置节,一次上三参数。
5. 设置位置:**per-Agent**,配置页运行预算组第 7 旋钮,`policies.token_budget`,0/缺省=关闭。无平台级默认、无调用级覆盖(YAGNI,用户知情后未要求)。
6. **护栏触发可见性 = guard marker 帧,连老闸一起治**(2026-07-20 追加拍板):现状三闸触发在界面上零标记(收尾轮看着像普通步,INTERRUPTED 不标原因)——token 闸 80% 预警/超限 + max_steps/no_progress 超限统一发 `guard` 事件帧(持久化,回放同源),时间线渲染成醒目 marker 行。

## 三闸并存语义(钉死)

互相独立,OR——谁先触发谁生效:

| 闸 | 维度 | 范围 | 检查点 | 触发行为 |
|---|---|---|---|---|
| `workflow.max_iterations` | 步数 | 每 agent 实例各自 | 每步 | 优雅收尾 |
| `policies.run_deadline_s` | 墙钟 | 全树一个截止点 | 委托边界(现状) | 拒绝委托/INTERRUPTED |
| `policies.token_budget`(新) | token | **全树一个池** | 每步 | 80% 预警→优雅收尾 |

步数闸 per 实例,管不住 16 worker × 32 步的乘积效应;token 池全树一个,正好补乘积面。

## PR1 —— 熔断全链

### Schema

`PolicySpec` 新增(`agent_spec.py` policies 块,`run_deadline_s` 旁):

```python
token_budget: int = Field(
    default=0,
    ge=0,
    description="Per-run total token cap across the whole delegation tree "
    "(main agent + static sub-agents + dynamic workers). Counts "
    "input+output+cache_creation+cache_read from each main-loop LLM call. "
    "0 disables the breaker.",
)
```

`BuiltAgent` 加派生字段 `token_budget: int = 0`(`trajectory_recording` 先例,spec 派生)。

### TokenBudget 运行时对象

新模块(orchestrator 内,与 `tools/_budget.py` WorkerSpawnBudget 相邻或同居):

```python
@dataclass
class TokenBudget:
    limit: int                    # >0
    spent: int = 0
    WARN_PCT: ClassVar[float] = 0.8

    def add(self, n: int) -> None            # 累加(单线程事件循环,无锁)
    @property
    def exhausted(self) -> bool              # spent >= limit
    @property
    def warning(self) -> bool                # spent >= limit * WARN_PCT(且未 exhausted)
    @property
    def remaining(self) -> int               # max(0, limit - spent)
```

### 数据流

```
run_agent(sse.py):limit = built.token_budget;limit>0 才建对象
  → effective_config.configurable[TOKEN_BUDGET_KEY] = budget   [key 放 tools 层防包环,WORKER_EVENT_SINK_KEY 先例]
  → agent_node(builder.py):
      每步 LLM 返回后(usage_metadata 在手,builder.py:944 区)budget.add(四项合计)
      步首判定:budget.exhausted 并入现有 budget_exhausted(builder.py:528 或运算)
        → 同款优雅收尾:tools=[] + token 版收尾指令(_TOKEN_BUDGET_WRAPUP_INSTRUCTION,
          镜像 _MAX_STEPS_WRAPUP_INSTRUCTION 文案改预算措辞),一轮总结,run 正常 SUCCESS
      budget.warning:在最后一条消息附预算注(已用/上限/剩余,镜像 ContextPressure;
        每步持续附注直到收尾;不碰 system 前缀)
  → _child_config(_child_run.py):budget 原样下传(deadline_at 先例)
      → worker/subagent 的 agent_node 同扣同判,各自收尾,全树收敛
```

细节钉死:

- 收尾轮自身不再受闸(允许小幅超支——收尾轮的 usage 照常 add,只是不再触发第二次收尾;`budget_exhausted` 现有机制天然如此)。
- 无 budget(limit=0 / 未注入)= 零行为变化(所有检查点先判 None)。
- 不加新 state channel、不加新 SSE 事件类型(每步 token 前端已显示;YAGNI)。
- 留痕:超限收尾时结构化日志 + Prometheus counter `expert_work_token_budget_exhausted_total`(labels: agent);80% 预警首次跨越时日志一条。
- 边界(声明,不修):辅助调用不进闸(本就无计量);escalated 主循环调用进闸(usage 在 agent_node 手里)。

### 护栏可见性:guard marker 帧(同 PR)

新 SSE 事件 `"guard"`(compaction sink 同款:run_agent 定义 `_publish_guard`(publish + `_persist_event`,seq 同步分配防并发——worker 树里的 guard 也走它),`GUARD_SINK_KEY` 注入 configurable,`_child_config` 下传;key 放 tools 层防包环,`WORKER_EVENT_SINK_KEY` 先例)。发射 best-effort(吞异常,不影响 run 本体)。

帧格式:

```json
{
  "kind": "warning" | "tripped",
  "guard": "token_budget" | "max_steps" | "no_progress",
  "detail": {"spent": 410000, "limit": 500000}      // token_budget
            | {"steps": 30, "max": 30}               // max_steps
            | {"streak": 3, "max": 3}                // no_progress
}
```

发射点(全在 agent_node,budget_exhausted 判定处一次全治):

- token 80% **首次跨越** → `warning` 一条(不逐步重发);
- token 超限进收尾 → `tripped`;
- max_steps / no_progress 超限进收尾 → 各自 `tripped`(治老盲区)。

前端:`parseTimeline` marker 分支加 `"guard"` → `MarkerItem` 新 kind(warning=warn 色 / tripped=danger 色),`StepTimeline` MarkerRow 渲染文案(i18n:如「⛔ token 预算耗尽(503k/500k)→ 收尾轮」);回放同路径。声明 out-of-scope:`run_deadline_s` 触发的 INTERRUPTED 原因标注(触发在委托边界 raise 路径,另一条链,记 backlog)。

### UI(同 PR)

运行预算组第 7 旋钮:`RUN_BUDGET_DEFS` 加 `{fieldId:"policies.token_budget", kind:"number", effectiveDefault:0, min:0}`;`form_model.ts` 的 `RunBudgetFields`/`readRunBudget`/`patchRunBudget` 扩展;i18n `run_budget.token_budget_*` 四键三处(label/brief/impact/default;impact 写明"全树共享一个池、80% 预警、超限优雅收尾、0=关闭");round-trip 测试。

### 测试(PR1)

- schema:token_budget round-trip、ge=0 校验。
- TokenBudget 单测:add/exhausted/warning/remaining 边界(恰好 80%、恰好 limit)。
- graph 集成(照 builder 现有测试惯例):
  - 超限→下一步无工具收尾轮→正常 END,run SUCCESS;
  - 80% 预警注入(最后一条消息含预算注,system 前缀不动);
  - 0=off:全链零行为变化;
  - 共扣:父 + child(经 _child_config)扣同一对象,child 超限收尾;
  - 收尾轮不二次触发。
- guard 帧:token 80% 首跨发一条 warning(不重发)、token/max_steps/no_progress 超限各发 tripped、帧落 RunEventStore seq 单调、sink 抛异常不影响 run、无 sink(未注入)零行为变化。
- 前端 guard marker:parseTimeline 解析 + MarkerRow 渲染(warn/danger 双态)+ 回放同源 vitest。
- UI:旋钮渲染/读写/round-trip vitest。
- 终门:CI 同款 pytest/ruff/mypy + admin-ui vitest+typecheck。

## PR2 —— dynamic_worker 平台配置节

照 `platform_tool_budget_config` 全套先例(DB-wins-over-env):

- persistence:`platform_dynamic_worker_config` model + migration + store(memory/sql)。
- control-plane:service(env 默认,DB 覆盖)+ `GET/PUT /v1/platform/dynamic-worker-config`(`is_system_admin` 门,`{success,data,error}` 信封)。
- 暴露三参数:`max_concurrent`(1-16)/`max_per_run`(1-64)/`max_iterations`(1-64)——校验界跟 settings.py 现有 Field 约束。
- 消费点改造:`app.py:1395` worker_build_fn 构建处改经 service 读取,**热生效**(per-build 读,不缓存进闭包)。
- admin-ui:`SettingsPlatformConfig` 加 `PlatformDynamicWorkerSection`(照 PlatformToolBudgetSection 形状)+ api client + i18n 三处。
- 测试:store DB-wins-over-env、权限门 403、PUT 校验界、消费点热生效(改 DB 后下一次 build 用新值)、UI 节 vitest。

## 交付切分

| PR | 内容 | 依赖 |
|---|---|---|
| PR1 | schema + TokenBudget + graph 接线 + UI 旋钮 + 全量测试 | 无 |
| PR2 | dynamic_worker 平台配置节全栈 | 无(与 PR1 独立) |

## 风险

| 风险 | 缓解 |
|---|---|
| budget_exhausted 或运算改动波及 max_steps 语义 | 现有 max_steps/no_progress 测试原样通过为红线 |
| 预算注干扰 prompt cache | 镜像 ContextPressure:只附最后一条消息,不碰 system 前缀 |
| 共享对象下传破坏 child 语义 | 镜像 deadline_at 键值下传,child 不重置;B2 的三处子图桩已有 astream 形状,测试沿用 |
| 热生效读 service 增加 build 延迟 | 单行 DB 读 per worker-build,量级可忽略;照 tool_budget 先例 |
