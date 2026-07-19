# B2 Worker 可观测性 —— worker 事件桥设计

> 日期:2026-07-19。Backlog P0(docs/BACKLOG.md)。交付:2 PR(PR1 后端桥,PR2 前端渲染)。

## 背景与根因

`spawn_worker` 是黑箱:worker 在父 run 的 tools_node 里**同进程嵌套** `child.graph.ainvoke`
(`services/orchestrator/src/orchestrator/tools/_child_run.py:107`),整个 ReAct 循环跑完才返回。
父 `graph.astream` 在此期间吐不出任何帧 —— 不是事件丢了,是父流真停摆,SSE 只剩心跳。
真实事故:run 141aa72d 被误判"卡住",实际 worker 正常干活 37 分钟后成功。

现状盘点(2026-07-19 溯源核实):

- 父流只见 `spawn_worker` 的 tool_call(agent 步 AIMessage)与 tool_result(ToolMessage,updates 帧),中间零信号。
- `SubAgentInvocation` 汇总(角色/迭代数/LLM 调用数/耗时)已随 tools_node updates 帧流到前端+落库
  (`state.py:212`,channel `subagent_invocations`),但前端 `parseTimeline` 不读 —— 数据扔在地上。
- worker 内部 LLM/工具 span 经 OTel 隐式上下文已嵌套进父 trace(`builder.py:2078-2094`),TraceView 可见但无 worker 标注。
- worker 另有独立 L7 轨迹记录(`_child_run.py:140`,keyed sub_run_id),与父 run 事件流无关。

## 决策纪要(用户 2026-07-19 拍板)

1. **持久化+回放**:worker 帧进 RunEventStore,实时与历史调试完全对齐,单一代码路径。
2. **每步桥+截断摘要**:worker 每个 agent 步/工具调用都成帧,内容截断;全文细节看 TraceView。
3. **UI = 工具卡内嵌子时间线**:spawn_worker 工具卡变可展开容器,worker 步进实时增长,孙 worker 递归嵌套。
4. **机制 = A(sink 注入 + astream 化)+ B 收尾行**(SubAgentInvocation 汇总作工具卡脚注)。

覆盖面:桥做在 `run_child_to_result` 共享核心,**spawn_worker 与静态 SubAgentTool 一次都治好**。

## 架构

### 事件流(改后)

```
parent run_agent (sse.py)
 ├─ 定义 _publish_worker(frame):同步分配 seq → bridge.publish("worker", frame)
 │                                → _persist_event(RunEventStore)   [best-effort,吞异常]
 ├─ effective_config.configurable[WORKER_EVENT_SINK_KEY] = _publish_worker
 └─ graph.astream(顶层图)
     └─ tools_node
         ├─ _tool_context 读 sink → ToolContext.worker_event_sink(仿 worker_spawn_budget)
         ├─ dispatch 处 replace(ctx, tool_call_id=call_id)(ToolContext 新字段)
         └─ SpawnWorkerTool.call / SubAgentTool.call
             └─ run_child_to_result
                 ├─ sink("start" 帧)
                 ├─ async for chunk in child.graph.astream(stream_mode=["updates","values"]):
                 │     updates chunk → 截断 → sink("update" 帧)
                 │     values chunk → 留作最终 state
                 ├─ sink("end" 帧)   [cancel 路径 best-effort 发完再 re-raise]
                 └─ _child_config 向下透传 sink → 孙 worker 帧同样直达父 bridge
```

### 关键实现点

| 点 | 内容 | 锚点 |
|---|---|---|
| sink 定义 | `_publish_worker`,照抄 compaction sink 模式但**先同步分配 seq 再 await**(见"seq 竞态") | `sse.py:364-374` 旁 |
| sink 注入 | `WORKER_EVENT_SINK_KEY` 进 `effective_config["configurable"]` | `sse.py:382-383` 旁 |
| ToolContext | 新字段 `worker_event_sink: Callable \| None = None`、`tool_call_id: str \| None = None` | `registry.py:154` |
| ctx 读取 | `_tool_context` 从 configurable 读 sink(仿 `worker_spawn_budget`,`builder.py:2540`) | `builder.py:2546` |
| call id | dispatch 处 `replace(ctx, tool_call_id=call_id)`(`call_id` 已在手,`builder.py:2065`) | `builder.py:2120` 附近 |
| astream 化 | `ainvoke` → `astream(stream_mode=["updates","values"])`;最后一个 values chunk = 最终 state,替代 ainvoke 返回值 | `_child_run.py:107` |
| 向下透传 | `_child_config` 把 sink 放进子 configurable(孙 worker 用) | `_child_run.py:339` |

### seq 竞态(必须防)

compaction sink 是 `await publish → await persist(seq) → seq += 1`,单发安全;
worker 并发最多 3(`dynamic_worker_max_concurrent`),两个 sink 交错会拿到同一 seq → PK (run_id, seq) 冲突。
`_publish_worker` 必须**先同步 `seq = event_seq; event_seq += 1`,后 await**。

### 行为等价性(astream 化的红线)

- 取消:cancellation token 经 `_child_config` 共享不变;`RunCancelledError` 从 astream 迭代中抛出,走原 except 路径(fetch partial + 轨迹 + re-raise),**新增**:re-raise 前 best-effort 发 `end(cancelled)` 帧。
- MaxSteps:`MaxStepsExceededError` 同样从迭代中抛出,原路径不变,end 帧 outcome=`max_steps`。
- 最终结果:values 流的最后一个 chunk 即 `ainvoke` 的返回值(LangGraph 语义);异常时 values 可能缺失 → 沿用现有 `_fetch_partial` 兜底(`_child_run.py:136`)。
- 现有全部 subagent/spawn_worker 测试必须原样通过(除显式断言 ainvoke 调用形状的 mock,允许 repoint)。

## 帧格式(SSE event: `worker`)

```json
{
  "worker_id": "<sub_run_id>",
  "parent_worker_id": null,
  "parent_tool_call_id": "<call_id>",
  "label": "spawn_worker",
  "agent_ref": "dynamic:research",
  "depth": 1,
  "kind": "start",
  "wseq": 0,
  "data": { }
}
```

- `worker_id` = sub_run_id;`parent_worker_id`:depth-1 worker 为 `null`,孙 worker 为父 worker 的 worker_id(前端建树用)。
- `parent_tool_call_id` = 触发本 worker 的那次 tool_call 的 id(前端挂 pending 工具卡)。
- `label`/`agent_ref`:动态 worker = `"spawn_worker"` / `"dynamic:<role>"`;静态 subagent = 工具名 / `"name@version"`(与 SubAgentInvocation 现字段同源)。
- `wseq`:worker 内局部单调序(0 起),前端排序用;全局顺序由外层 RunEventStore seq 保证。
- `kind` 三值:
  - **start** `data = {task_excerpt(≤500 字), role, max_steps}`
  - **update** `data = {node, step_count, _duration_ms, messages: [截断摘要]}`
    消息摘要:`{type, content_excerpt(≤500 字), tool_calls: [{name, args_excerpt(≤200 字)}], tool_result_excerpt(≤500 字)}`,
    按 chunk 内实有字段取舍;非消息类 writes(plan 等)丢弃不桥。
  - **end** `data = {outcome: "success"|"max_steps"|"cancelled", iteration_used, llm_call_count, wall_clock_ms}`
    (与 SubAgentInvocation 同款汇总;cancel 路径 best-effort 发完再 re-raise)。

截断常量:`WORKER_CONTENT_EXCERPT = 500`、`WORKER_ARGS_EXCERPT = 200`、`WORKER_RESULT_EXCERPT = 500`(字符数,超限截断加 `…`)。

## 持久化与量控

- worker 帧走 `_persist_event` 同路径落 RunEventStore,回放端点天然重放,历史轮时间线与实时一致。
- **量控 = 截断,不采样不分级**:现有硬顶(per-run ≤16 worker、迭代 ≤32/64、深度 ≤3、并发 ≤3)下
  最坏 ~2k 帧 × ~2KB ≈ 几 MB/run,可控。显式决策:不加新 cap。
- sink 全程 best-effort:发布/落库异常吞掉记日志,桥接故障绝不影响 worker 本体执行。
- 脱敏面不新增:worker 摘要与父 updates 帧同类(持久化转录本体),不走 token 通道的流式脱敏;worker 无 token sink,token 帧不桥(维持现状)。

## 前端(PR2)

- `apps/admin-ui/src/api/timeline.ts`:解析 `event === "worker"` 帧;按 `worker_id` 聚合成
  `WorkerTimeline {worker_id, label, role, depth, status, steps[], summary?}`,经 `parent_tool_call_id`
  挂到对应 `ToolCallEntry`;`parent_worker_id` 非空的挂到父 worker 的对应步(树)。
- `StepTimeline.tsx` 工具卡:有 worker 子时间线 → 可展开容器(默认收起,运行中徽章提示),
  worker 步进/工具调用作嵌套子项 live 增长;孙 worker 递归同款。
- `end` 帧 → 卡片脚注汇总行(迭代/LLM 调用/耗时/outcome)。历史回放同 parse 同渲染,零分叉。
- worker 帧进正常事件流(非 token 通道),频率低(每步一帧),无 memo 稳定性顾虑(3a token 帧的
  分流手法此处**不需要**)。
- run_detail `EventStreamPanel` 零改动(worker 帧照 dump);TraceView 零改动(span 嵌套已有)。
- i18n:新键三处(interface + en + zh-CN),先 grep 撞键。

## 兼容性

- PR1 先合:老前端对未知 `worker` 帧天然忽略(switch default),无兼容问题。
- 回放:老 run 无 worker 帧,前端渲染同今天(工具卡无展开区);新 run 帧齐全。
- 事件序:worker 帧落在 spawn_worker 所在 tools_node 的 updates 帧**之前**(与 compaction 同语义
  ——node 执行中发布,先于该轮 updates chunk),前端不依赖 worker 帧晚于 tool_result。

## 测试

PR1(orchestrator pytest,`uv run pytest`):

- worker 跑完:start / update×N / end 帧齐全,RunEventStore seq 单调无洞,wseq 0..N 单调。
- 并发两 worker(parallel tool batch):seq 不撞、按 worker_id 可分拣。
- cancel:end(cancelled) 帧发出后 `RunCancelledError` 照旧 re-raise。
- MaxSteps:end(max_steps),ToolResult 仍为 partial result 语义。
- 孙 worker:depth=2 帧带 parent_worker_id。
- sink 抛异常:worker 本体照常成功(best-effort)。
- 等价性:现有 subagent/spawn_worker 全部测试原样通过。
- 截断:超长 content/args/result 截到常量上限。

PR2(admin-ui vitest + typecheck):

- parseTimeline:worker 帧聚合、挂 ToolCallEntry、孙 worker 建树、end 汇总。
- StepTimeline:展开容器渲染、运行中/结束态、脚注行。
- 回放路径(历史事件数组一次性喂入)与 live 逐帧路径产出一致。

## 交付切分

- **PR1 后端桥**:sink + ToolContext 字段 + astream 化 + 持久化 + 全量测试。合并即 raw 事件面板可见 worker 帧(dump)。
- **PR2 前端渲染**:timeline 解析 + 嵌套 UI + 脚注 + i18n。建在 PR1 上。

## 风险

| 风险 | 缓解 |
|---|---|
| astream 化改变 child 运行语义 | 等价性红线 + 现有测试全过 + cancel/MaxSteps 显式测试 |
| seq 竞态 | sink 同步分配 seq;并发 worker 显式测试 |
| 事件量失控 | 截断常量 + 现有四重硬顶;不加新面 |
| 桥接故障拖垮 worker | sink best-effort,吞异常记日志 |
| 前端 memo 抖动 | worker 帧频率低走正常事件流;不复用 token 分流(YAGNI) |
