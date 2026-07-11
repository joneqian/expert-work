# 调试台历史对话调试视图重建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** resume 载入的历史对话轮渲染成与实时轮同构的完整 `TurnCard`(全套调试面板),懒填充、只读、失败降级。

**Architecture:** 新增 `GET /v1/sessions/{thread_id}/runs` 列 thread 的 runs;前端把历史 `/messages` 的用户/助手文本对与 runs 按序配对(计数守卫)成历史轮描述符;每轮渲染为只读 `TurnCard`,IntersectionObserver 滚到可见时 `streamRunEvents(runId)` 回放事件填充完整面板,任一环节失败回退扁平文本。

**Tech Stack:** 后端 FastAPI(control-plane,`uv`);前端 React + Vite + AntD 5 + react-i18next(`pnpm`/`vitest`)。

## Global Constraints

- admin-ui:`pnpm typecheck`(tsc -b)0 报错;`npx vitest run` 全绿。
- i18n 三处同步(编译器强制):`en.ts` 的 `TranslationKeys` 接口 **+** `en.ts` 的 `en` 值 **+** `zh-CN.ts` 的值。
- 后端:`uv run pytest` / `uv run mypy` / `uv run ruff check` 全绿(repo-root 配置)。**提交前本地跑 `uv run ruff check`**(本地 mypy 过 ≠ ruff 过)。
- 只读安全:历史轮**不得**对已结束 run 发任何可变请求(审批/反馈/重跑)。
- 降级:任一环节失败回退扁平文本气泡,**不 500、不白屏、不丢已有内容**。
- 复用现成 `TurnCard` 渲染路径,**不 fork** 平行渲染实现。
- 端点契约:`{run_id, status, is_resume, created_at}`,**oldest-first**。
- 命令工作目录:后端 `services/control-plane`,前端 `apps/admin-ui`。

---

### Task 1: 后端端点 `GET /v1/sessions/{thread_id}/runs`

**Files:**
- Modify: `services/control-plane/src/control_plane/api/runs.py`(在 `get_thread_messages`(`@router.get("/{thread_id}/messages")`,约 1190-1270)之后新增一个 handler)
- Test: `services/control-plane/tests/test_runs_api.py`(复用现有 `runs_client` fixture / `_create_session`)

**Interfaces:**
- Consumes:`RunStore.list_by_thread(*, thread_id: UUID, tenant_id: UUID) -> list[RunInfo]`(oldest-first,已存在);owner 门控 helper `resolve_caller_user_id` / `caller_owns_thread`(runs.py 已 import);`RunStatus`(`.value` 取字符串)。
- Produces:端点 `GET /v1/sessions/{thread_id}/runs` → `{"success": true, "data": {"runs": [{"run_id": str, "status": str, "is_resume": bool, "created_at": iso8601}...]}}`,oldest-first。

- [ ] **Step 1: 写失败测试(加到 `tests/test_runs_api.py` 末尾)**

```python
@pytest.mark.asyncio
async def test_thread_runs_404_for_unknown(runs_client: AsyncClient) -> None:
    resp = await runs_client.get("/v1/sessions/00000000-0000-0000-0000-0000000000ff/runs")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_thread_runs_empty_for_fresh_thread(runs_client: AsyncClient) -> None:
    thread_id = await _create_session(runs_client)
    resp = await runs_client.get(f"/v1/sessions/{thread_id}/runs")
    assert resp.status_code == 200
    assert resp.json()["data"]["runs"] == []


@pytest.mark.asyncio
async def test_thread_runs_lists_oldest_first(runs_client: AsyncClient) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from expert_work.runtime.runs import DisconnectMode, RunInfo, RunStatus

    thread_id = await _create_session(runs_client)
    app = runs_client._transport.app  # type: ignore[attr-defined,union-attr]
    base = datetime.now(UTC)
    older, newer = uuid4(), uuid4()
    # Seed newer first to prove the endpoint sorts by created_at, not insert order.
    for rid, created, is_resume in (
        (newer, base + timedelta(seconds=61), True),
        (older, base, False),
    ):
        await app.state.run_store.create(
            RunInfo(
                run_id=rid,
                tenant_id=DEFAULT_DEV_TENANT_ID,
                thread_id=UUID(thread_id),
                user_id=None,
                status=RunStatus.SUCCESS,
                on_disconnect=DisconnectMode.CANCEL,
                is_resume=is_resume,
                error=None,
                created_at=created,
                updated_at=created,
                finished_at=created,
            )
        )
    resp = await runs_client.get(f"/v1/sessions/{thread_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()["data"]["runs"]
    assert [r["run_id"] for r in runs] == [str(older), str(newer)]
    assert [r["is_resume"] for r in runs] == [False, True]
    assert runs[0]["status"] == "success"
    assert "created_at" in runs[0]


@pytest.mark.asyncio
async def test_thread_runs_foreign_tenant_forbidden(runs_client: AsyncClient) -> None:
    thread_id = await _create_session(runs_client)
    resp = await runs_client.get(
        f"/v1/sessions/{thread_id}/runs",
        params={"tenant_id": "11111111-1111-1111-1111-111111111111"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd services/control-plane && uv run pytest tests/test_runs_api.py -k thread_runs -v`
Expected: 4 条 FAIL(端点 404 Not Found — 路由不存在)。

- [ ] **Step 3: 实现端点(runs.py,紧接 `get_thread_messages` 之后)**

参照 `get_thread_messages` 的门控与信封,写:

```python
    @router.get("/{thread_id}/runs", response_model=None)
    async def list_thread_runs(
        thread_id: UUID,
        request: Request,
        threads: Annotated[object, Depends(_get_thread_repo)],
        users: Annotated[TenantUserStore, Depends(get_user_repo)],
        runs: Annotated[RunStore, Depends(_get_run_store)],
        audit: Annotated[AuditLogger, Depends(_get_audit)],
        tenant_id: Annotated[UUID | None, Query()] = None,
    ) -> JSONResponse:
        """Playground history reconstruction — the thread's runs, oldest-first.

        Lets the debug console lazily replay each past run's event stream
        (``GET .../runs/{run_id}/events``) to rebuild a full historical turn.
        Ownership-gated identically to ``get_thread_messages``; a concrete
        ``tenant_id`` lets a system_admin read a foreign tenant's runs.
        Returns ``run_id`` / ``status`` / ``is_resume`` / ``created_at`` only —
        the debug payload lives in the per-run event replay, not here.
        """
        scope = await ensure_tenant_scope(
            request.state.principal,
            tenant_id,
            audit,
            trace_id=current_trace_id_hex(),
            endpoint="GET /v1/sessions/{thread_id}/runs",
            cross_tenant_enabled=cross_tenant_query_enabled(request),
        )
        if isinstance(scope, CrossTenant):
            raise HTTPException(
                status_code=422,
                detail="a thread belongs to one tenant; pass a concrete tenant_id",
            )
        target_tenant = scope.tenant_id
        meta = await threads.get(thread_id, tenant_id=target_tenant)  # type: ignore[attr-defined]
        if meta is None:
            raise HTTPException(status_code=404, detail="session not found")
        caller_user_id = await resolve_caller_user_id(request, users)
        if not caller_owns_thread(
            meta=meta, caller_user_id=caller_user_id, principal=request.state.principal
        ):
            raise HTTPException(status_code=404, detail="session not found")

        rows = await runs.list_by_thread(thread_id=thread_id, tenant_id=target_tenant)
        out = [
            {
                "run_id": str(r.run_id),
                "status": r.status.value,
                "is_resume": r.is_resume,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        return JSONResponse({"success": True, "data": {"runs": out}})
```

注:所有引用的名字(`ensure_tenant_scope` / `CrossTenant` / `cross_tenant_query_enabled` / `current_trace_id_hex` / `_get_run_store` / `RunStore` / `get_user_repo` / `TenantUserStore` / `_get_audit` / `AuditLogger`)在 `get_thread_messages` 中均已使用/import,无需新增 import。与 `/messages` 不同,此端点**不**发 SESSION_READ 审计行(它不返回对话内容,只返回 run 元数据;跨租户抽查已由 `ensure_tenant_scope` 记录)。

- [ ] **Step 4: 跑测试确认 PASS**

Run: `cd services/control-plane && uv run pytest tests/test_runs_api.py -k thread_runs -v`
Expected: 4 PASS。

- [ ] **Step 5: mypy + ruff**

Run: `cd services/control-plane && uv run mypy src/control_plane/api/runs.py && uv run ruff check`
Expected: 均 clean。

- [ ] **Step 6: Commit**

```bash
git add services/control-plane/src/control_plane/api/runs.py services/control-plane/tests/test_runs_api.py
git commit -m "feat(playground): 加 GET /v1/sessions/{thread_id}/runs 端点(历史调试重建用)"
```

---

### Task 2: 前端 SDK `listThreadRuns`

**Files:**
- Modify: `apps/admin-ui/src/api/runs.ts`
- Test: `apps/admin-ui/src/api/__tests__/runs.test.ts`(若不存在则新建)

**Interfaces:**
- Consumes:Task 1 端点契约 `{run_id, status, is_resume, created_at}` oldest-first;`apiClient`(已 import);现有 `ApiEnvelope`/`unwrap`(见 `api/sessions.ts` 用法)。
- Produces:
  - `interface ThreadRunSummary { runId: string; status: RunStatus; isResume: boolean; createdAt: string }`
  - `async function listThreadRuns(threadId: string, tenantId?: string): Promise<ThreadRunSummary[]>`

- [ ] **Step 1: 写失败测试**

在 `apps/admin-ui/src/api/__tests__/runs.test.ts`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";

import { apiClient } from "../client";
import { listThreadRuns } from "../runs";

vi.mock("../client", () => ({
  apiClient: { get: vi.fn() },
}));

describe("listThreadRuns", () => {
  beforeEach(() => vi.mocked(apiClient.get).mockReset());

  it("GETs the thread runs endpoint and maps to camelCase", async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        success: true,
        data: {
          runs: [
            { run_id: "r1", status: "success", is_resume: false, created_at: "2026-01-01T00:00:00Z" },
            { run_id: "r2", status: "paused", is_resume: true, created_at: "2026-01-01T00:01:00Z" },
          ],
        },
        error: null,
      },
    });
    const runs = await listThreadRuns("t1");
    expect(apiClient.get).toHaveBeenCalledWith("/v1/sessions/t1/runs", {
      params: undefined,
    });
    expect(runs).toEqual([
      { runId: "r1", status: "success", isResume: false, createdAt: "2026-01-01T00:00:00Z" },
      { runId: "r2", status: "paused", isResume: true, createdAt: "2026-01-01T00:01:00Z" },
    ]);
  });

  it("passes tenant_id when given", async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { success: true, data: { runs: [] }, error: null },
    });
    await listThreadRuns("t1", "ten-9");
    expect(apiClient.get).toHaveBeenCalledWith("/v1/sessions/t1/runs", {
      params: { tenant_id: "ten-9" },
    });
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/runs.test.ts`
Expected: FAIL(`listThreadRuns` 未导出)。

- [ ] **Step 3: 实现(api/runs.ts 末尾追加)**

`api/runs.ts` 顶部目前只 `import { apiClient } from "./client";`。`ApiEnvelope` 与 `unwrap` 都在 `./client`(见 `api/sessions.ts:19-20`)。把 runs.ts 的顶部 import 改为:

```typescript
import { apiClient, unwrap, type ApiEnvelope } from "./client";
```

```typescript
/** Playground history reconstruction — one row of ``GET
 *  /v1/sessions/{thread_id}/runs``. ``createdAt`` orders turns; ``runId``
 *  feeds the per-run event replay that rebuilds a full historical turn. */
export interface ThreadRunSummary {
  runId: string;
  status: RunStatus;
  isResume: boolean;
  createdAt: string;
}

interface ThreadRunRow {
  run_id: string;
  status: RunStatus;
  is_resume: boolean;
  created_at: string;
}

/** List a thread's runs oldest-first. ``tenantId`` (a system_admin drilling
 *  into a foreign tenant) is a no-op for a caller's own tenant. */
export async function listThreadRuns(
  threadId: string,
  tenantId?: string,
): Promise<ThreadRunSummary[]> {
  const response = await apiClient.get<ApiEnvelope<{ runs: ThreadRunRow[] }>>(
    `/v1/sessions/${threadId}/runs`,
    { params: tenantId ? { tenant_id: tenantId } : undefined },
  );
  return unwrap(response.data).runs.map((r) => ({
    runId: r.run_id,
    status: r.status,
    isResume: r.is_resume,
    createdAt: r.created_at,
  }));
}
```

- [ ] **Step 4: 跑测试 + typecheck 确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/api/__tests__/runs.test.ts && pnpm typecheck`
Expected: 测试 PASS,typecheck 0 报错。

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/api/runs.ts apps/admin-ui/src/api/__tests__/runs.test.ts
git commit -m "feat(playground): listThreadRuns SDK(列 thread runs)"
```

---

### Task 3: `buildHistoryTurns` 纯函数

**Files:**
- Create: `apps/admin-ui/src/pages/agent_detail/playground/history_turns.ts`
- Test: `apps/admin-ui/src/pages/agent_detail/playground/__tests__/history_turns.test.ts`

**Interfaces:**
- Consumes:`HistoryMessage`(`api/sessions.ts`,`{role:"user"|"assistant"; content:string}`);`ThreadRunSummary`(Task 2)。
- Produces:
  - `interface HistoryTurn { key: string; input: string; fallbackAnswer: string; runId: string; status: string }`
  - `function buildHistoryTurns(messages: readonly HistoryMessage[], runs: readonly ThreadRunSummary[]): HistoryTurn[] | null`
  - 语义:把 `messages` 折成 `(user, 紧随 assistant)` 对(assistant 缺失时空串);若 `对数 !== runs.length` 返回 `null`(调用方降级扁平文本);否则第 i 对配 `runs[i]`(runs 已 oldest-first)。

- [ ] **Step 1: 写失败测试**

```typescript
import { describe, expect, it } from "vitest";

import type { HistoryMessage } from "../../../../api/sessions";
import type { ThreadRunSummary } from "../../../../api/runs";
import { buildHistoryTurns } from "../history_turns";

function run(runId: string): ThreadRunSummary {
  return { runId, status: "success", isResume: false, createdAt: "2026-01-01" };
}

const U = (content: string): HistoryMessage => ({ role: "user", content });
const A = (content: string): HistoryMessage => ({ role: "assistant", content });

describe("buildHistoryTurns", () => {
  it("pairs each (user, following assistant) with the i-th run in order", () => {
    const turns = buildHistoryTurns(
      [U("q1"), A("a1"), U("q2"), A("a2")],
      [run("r1"), run("r2")],
    );
    expect(turns).toEqual([
      { key: "r1", input: "q1", fallbackAnswer: "a1", runId: "r1", status: "success" },
      { key: "r2", input: "q2", fallbackAnswer: "a2", runId: "r2", status: "success" },
    ]);
  });

  it("returns null when user-turn count != run count (approval split / stray runs)", () => {
    // 2 user turns, 3 runs (an approval split one turn into 2 runs) → degrade.
    expect(
      buildHistoryTurns([U("q1"), A("a1"), U("q2"), A("a2")], [run("r1"), run("r2"), run("r3")]),
    ).toBeNull();
  });

  it("tolerates a trailing user turn with no assistant reply (empty fallback)", () => {
    const turns = buildHistoryTurns([U("q1"), A("a1"), U("q2")], [run("r1"), run("r2")]);
    expect(turns?.[1]).toEqual({
      key: "r2",
      input: "q2",
      fallbackAnswer: "",
      runId: "r2",
      status: "success",
    });
  });

  it("returns [] for an empty thread", () => {
    expect(buildHistoryTurns([], [])).toEqual([]);
  });
});
```

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/history_turns.test.ts`
Expected: FAIL(`buildHistoryTurns` 未定义)。

- [ ] **Step 3: 实现 `history_turns.ts`**

```typescript
/**
 * Pair a resumed thread's flat message history with its runs so each past
 * turn can be rebuilt as a full (lazy) TurnCard. The run event stream does
 * NOT carry the user input (it's the graph input, kept in the checkpoint),
 * so the input text comes from ``/messages`` here, paired to the run that
 * produced it by ORDER — user turn ``i`` ↔ ``runs[i]`` (runs oldest-first).
 *
 * ``is_resume`` is deliberately ignored: it means "not the thread's first
 * run", not "approval continuation", so it can't delimit turns. A count
 * mismatch (an approval that split one turn across 2 runs, an auto-triggered
 * or errored run) is the honest signal that order-pairing is unsafe — we
 * return ``null`` and the caller falls back to flat text.
 */
import type { HistoryMessage } from "../../../api/sessions";
import type { ThreadRunSummary } from "../../../api/runs";

export interface HistoryTurn {
  key: string;
  input: string;
  fallbackAnswer: string;
  runId: string;
  status: string;
}

export function buildHistoryTurns(
  messages: readonly HistoryMessage[],
  runs: readonly ThreadRunSummary[],
): HistoryTurn[] | null {
  const pairs: { input: string; answer: string }[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const m = messages[i];
    if (m.role !== "user") continue;
    const next = messages[i + 1];
    const answer = next && next.role === "assistant" ? next.content : "";
    pairs.push({ input: m.content, answer });
  }
  if (pairs.length !== runs.length) return null;
  return pairs.map((p, i) => ({
    key: runs[i].runId,
    input: p.input,
    fallbackAnswer: p.answer,
    runId: runs[i].runId,
    status: runs[i].status,
  }));
}
```

- [ ] **Step 4: 跑测试 + typecheck 确认 PASS**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/playground/__tests__/history_turns.test.ts && pnpm typecheck`
Expected: 4 PASS,typecheck 0 报错。

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/playground/history_turns.ts apps/admin-ui/src/pages/agent_detail/playground/__tests__/history_turns.test.ts
git commit -m "feat(playground): buildHistoryTurns 配对纯函数(计数守卫)"
```

---

### Task 4: TurnCard `readOnly` + 懒态占位 + i18n 键

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`(`TurnCard` 组件,约 1675-1965)
- Modify: `apps/admin-ui/src/i18n/locales/en.ts`(接口 + 值)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts`(值)

**Interfaces:**
- Consumes:现有 `TurnCard` props + `Turn` 接口(`events` 驱动);AntD `Spin`(已可从 antd import)。
- Produces:`TurnCard` 新增可选 props:
  - `readOnly?: boolean`(default false)— 隐藏审批门(`ApprovalGate`,约 1945)、`FeedbackBar`(约 1962)及任何可变控件;保留导出/trace/事件视图切换。
  - `loadState?: "pending" | "loading" | "done" | "error"`(default `"done"`)。
  - `fallbackAnswer?: string` — 事件未到位时显示的助手文本。
  - 行为:当 `readOnly && turn.events.length === 0 && loadState !== "done"` → 渲染**占位卡**(输入气泡 + `fallbackAnswer` + `loadState` 为 pending/loading 时一个 `Spin` + `t("playground.history_loading")`;error 时无 Spin,直接扁平文本)。否则走原有完整渲染。

- [ ] **Step 1: 加 i18n 键(三处)**

`en.ts` 的 `TranslationKeys` 接口 `playground` 段(紧邻 `history_divider: string;`,约 705)加:

```typescript
    history_loading: string;
```

`en.ts` 的 `en` 值 `playground` 段(紧邻 `history_divider: "— new messages below —",`,约 3118)加:

```typescript
    history_loading: "Loading debug data…",
```

`zh-CN.ts` 对应 `playground` 段(紧邻其 `history_divider` 值)加:

```typescript
    history_loading: "载入调试数据…",
```

- [ ] **Step 2: 写失败测试(占位 + 只读)**

在 `apps/admin-ui/src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx` 加(若 TurnCard 未单独导出,则此断言在 Task 5 的集成测试里覆盖;本步先写一个能驱动 TurnCard props 的最小渲染测试。若 TurnCard 非导出且难独测,将本 Task 的测试并入 Task 5 并在此标注)。最小意图:

```typescript
// 意图断言(具体挂载方式对齐该测试文件既有 render helper):
// 1) readOnly + loadState="loading" + events=[] + fallbackAnswer="hi"
//    → 渲染 fallbackAnswer 文本 + history_loading 文案;不渲染审批按钮
//      (queryByTestId("playground-approval-approve") 为空)。
// 2) readOnly + loadState="done" + events=[...一个 end 帧...]
//    → 正常渲染,不出现 history_loading。
```

> 说明:`TurnCard` 目前是模块内私有函数。为可测,Task 5 会在 PlaygroundTab 内以历史轮路径挂载它;若本步无法独立挂载 `TurnCard`,把这两条断言移入 Task 5 的集成测试(见 Task 5 Step 1),本 Task 只做实现 + 由 Task 5 覆盖。实现者按实际可测性择一,并在提交信息注明。

- [ ] **Step 3: 实现 `readOnly` / 占位**

在 `TurnCard` 参数解构与类型注解处加三个可选 props(`readOnly = false`, `loadState = "done"`, `fallbackAnswer`)。在 `TurnCard` 函数体**最前面**(`summarizeTurn` 等解析之前)加占位早返回:

```tsx
  // 历史轮懒态:事件未到位时显示输入 + 兜底答案 + 载入指示,避免用空 events
  // 跑完整解析机器。事件到位(loadState==="done")后走下方原完整渲染。
  if (readOnly && turn.events.length === 0 && loadState !== "done") {
    return (
      <div
        data-testid="playground-turn"
        style={{ display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}
      >
        <div
          style={{
            alignSelf: "flex-end",
            maxWidth: "85%",
            padding: "6px 10px",
            borderRadius: 8,
            fontSize: 13,
            whiteSpace: "pre-wrap",
            background: "var(--ew-surface-raised)",
            border: "1px solid var(--ew-border-subtle)",
          }}
        >
          {turn.input}
        </div>
        {fallbackAnswer ? (
          <div
            style={{
              alignSelf: "flex-start",
              maxWidth: "85%",
              fontSize: 13,
              whiteSpace: "pre-wrap",
              opacity: 0.75,
            }}
          >
            <MarkdownView>{fallbackAnswer}</MarkdownView>
          </div>
        ) : null}
        {loadState !== "error" ? (
          <div
            style={{ display: "flex", alignItems: "center", gap: 6, opacity: 0.6, fontSize: 12 }}
          >
            <Spin size="small" />
            <span>{t("playground.history_loading")}</span>
          </div>
        ) : null}
      </div>
    );
  }
```

然后把审批门与反馈栏挂在 `!readOnly` 后面:

```tsx
        {!readOnly && turn.approval && threadId && (
          <ApprovalGate ... />   // 原有内容不变,仅前面加 !readOnly &&
        )}
```
```tsx
        {!readOnly && turn.status === "done" && threadId && (
          <FeedbackBar threadId={threadId} turnSeq={turnSeq} />
        )}
```

确认顶部已 import `Spin`(antd)与 `MarkdownView`(history 块已在用 `MarkdownView`,应已 import)。若 `Spin` 未 import,加到现有 `antd` import 列表。

- [ ] **Step 4: typecheck + 相关测试**

Run: `cd apps/admin-ui && pnpm typecheck && npx vitest run src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx`
Expected: typecheck 0 报错(i18n 三处齐了才会过);现有测试仍绿。

- [ ] **Step 5: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/i18n/locales/en.ts apps/admin-ui/src/i18n/locales/zh-CN.ts
git commit -m "feat(playground): TurnCard 加 readOnly + 懒态占位(历史轮用)"
```

---

### Task 5: PlaygroundTab 历史懒重建接线

**Files:**
- Modify: `apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx`
- Test: `apps/admin-ui/src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx`

**Interfaces:**
- Consumes:`listThreadRuns`(Task 2)、`buildHistoryTurns`/`HistoryTurn`(Task 3)、`TurnCard` 的 `readOnly`/`loadState`/`fallbackAnswer`(Task 4)、现有 `getSessionMessages`/`streamRunEvents`/`SseEvent`。
- Produces:resume 载入的历史轮以只读懒填充 `TurnCard` 呈现;计数守卫失败或任一环节失败回退现有扁平文本块。

- [ ] **Step 1: 写失败测试(集成 resume 路径)**

在 `PlaygroundTab.test.tsx`(已有 `getMessagesMock = vi.spyOn(sessionsSdk, "getSessionMessages")`)加。需 mock `listThreadRuns`、`streamRunEvents`,并 shim `IntersectionObserver`(jsdom 无)。断言:

```typescript
// setup 顶部(测试文件级):
// - 全局 shim:立即把被 observe 的元素当作可见触发一次回调。
class IOStub {
  private cb: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) { this.cb = cb; }
  observe = (el: Element) => {
    this.cb(
      [{ isIntersecting: true, target: el } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  };
  unobserve = () => {};
  disconnect = () => {};
  takeRecords = () => [];
  root = null; rootMargin = ""; thresholds = [];
}
// beforeEach: vi.stubGlobal("IntersectionObserver", IOStub);

// 测试 A:计数相等 → 懒 TurnCard 回放填充
//   getSessionMessages → [user "q1", assistant "a1"]
//   listThreadRuns → [{runId:"r1",...}]
//   streamRunEvents("t","r1") → async* yields 一个含 AI 文本的 updates 帧 + end 帧
//   resume 该 thread → 等待 → 断言最终出现回放来的答案(非仅 fallback),
//   且无 history_loading(已 done),无审批按钮。

// 测试 B:计数不等 → 扁平降级
//   getSessionMessages → [user,assistant,user,assistant](2 轮)
//   listThreadRuns → [r1,r2,r3](3 run)
//   resume → 断言渲染现有扁平历史块(data-testid="playground-history"),
//   不出现懒 TurnCard 的 history_loading。

// 测试 C:回放失败 → 保留 fallback
//   listThreadRuns → [r1];streamRunEvents 抛错
//   resume → 断言显示 fallbackAnswer "a1" 文本,不白屏,无审批按钮。
```

(具体挂载/等待对齐该文件既有 render + `waitFor` helper。)

- [ ] **Step 2: 跑测试确认 FAIL**

Run: `cd apps/admin-ui && npx vitest run src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx`
Expected: 新 3 条 FAIL。

- [ ] **Step 3: 实现接线**

3a. 顶部 import 补:

```typescript
import { listThreadRuns, type ThreadRunSummary } from "../../api/runs";
import { buildHistoryTurns, type HistoryTurn } from "./playground/history_turns";
```
(`streamRunEvents` 已 import;`getSessionMessages` 已 import;`SseEvent` 类型已可用。)

3b. 新增懒态类型 + state:

```typescript
type HistoryLoad =
  | { state: "pending" | "loading" | "error"; events: SseEvent[] }
  | { state: "done"; events: SseEvent[] };

// 组件内:
const [historyTurns, setHistoryTurns] = useState<HistoryTurn[] | null>(null);
const [historyLoads, setHistoryLoads] = useState<Record<string, HistoryLoad>>({});
const historyAbortRef = useRef<AbortController | null>(null);
```

3c. `handleResume` 里,把现有 `getSessionMessages(...).then(setHistory)` 替换为并行拉取 + 配对(保留 `setHistory` 作降级路径的数据源):

```typescript
      // 历史重建:并行拉文本轮 + runs,按序配对成懒重建描述符;
      // 计数守卫失败或任一失败 → 落回扁平文本(setHistory 保留原行为)。
      setHistoryTurns(null);
      setHistoryLoads({});
      historyAbortRef.current?.abort();
      const ac = new AbortController();
      historyAbortRef.current = ac;
      void Promise.all([
        getSessionMessages(picked.thread_id),
        listThreadRuns(picked.thread_id).catch(() => null),
      ])
        .then(([messages, runs]) => {
          setHistory(messages); // 降级路径永远有数据
          if (!runs) return;
          const built = buildHistoryTurns(messages, runs);
          if (!built) return; // 计数守卫失败 → 保持扁平文本
          setHistoryTurns(built);
          setHistoryLoads(
            Object.fromEntries(built.map((h) => [h.runId, { state: "pending", events: [] }])),
          );
        })
        .catch(() => {
          setHistory([]);
          setHistoryTurns(null);
        });
```

同时在 `handleResume` 顶部的重置区把 `setHistoryTurns(null); setHistoryLoads({});` 一并清掉(与 `setHistory([])` 并列),`abortRef` 那套不变。

3d. 懒回放函数 + IntersectionObserver:

```typescript
  const replayHistoryRun = useCallback(
    async (runId: string, threadId: string) => {
      setHistoryLoads((prev) =>
        prev[runId]?.state === "pending" ? { ...prev, [runId]: { state: "loading", events: [] } } : prev,
      );
      try {
        const collected: SseEvent[] = [];
        for await (const frame of streamRunEvents(threadId, runId, {
          signal: historyAbortRef.current?.signal,
        })) {
          collected.push(frame);
          if (frame.event === "end") break;
        }
        setHistoryLoads((prev) => ({ ...prev, [runId]: { state: "done", events: collected } }));
      } catch {
        setHistoryLoads((prev) => ({ ...prev, [runId]: { state: "error", events: [] } }));
      }
    },
    [],
  );

  // 每个历史轮容器的 ref 回调注册到一个共享 observer;可见且 pending → 回放。
  const observerRef = useRef<IntersectionObserver | null>(null);
  const runIdByEl = useRef<Map<Element, string>>(new Map());
  const registerHistoryRow = useCallback(
    (runId: string, threadId: string) => (el: HTMLDivElement | null) => {
      if (observerRef.current === null) {
        observerRef.current = new IntersectionObserver((entries) => {
          for (const e of entries) {
            if (!e.isIntersecting) continue;
            const rid = runIdByEl.current.get(e.target);
            if (rid) void replayHistoryRun(rid, threadId);
          }
        });
      }
      if (el === null) return;
      runIdByEl.current.set(el, runId);
      observerRef.current.observe(el);
    },
    [replayHistoryRun],
  );
```

> 注:`threadId` 在 observer 闭包里取自 registerHistoryRow 的参数(当前 resume 的 thread 固定)。resume 切换会 `historyAbortRef.abort()` 掐断在途回放;新 thread 重建 historyTurns 时旧 observer 的 target 已卸载。实现者确认 observer 在 historyTurns 变更/卸载时 `disconnect()` 重建(可用 `useEffect(cleanup)` 或在 `handleResume` 重置里 `observerRef.current?.disconnect(); observerRef.current = null; runIdByEl.current.clear();`)。

3e. 渲染:把现有 `history.length > 0` 扁平块改为 —— `historyTurns !== null` 时渲染懒 `TurnCard` 列表,否则回退现有扁平块:

```tsx
          {historyTurns !== null ? (
            <div
              data-testid="playground-history"
              style={{ display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}
            >
              {historyTurns.map((h, idx) => {
                const load = historyLoads[h.runId] ?? { state: "pending", events: [] };
                return (
                  <div key={h.key} ref={registerHistoryRow(h.runId, thread?.thread_id ?? "")}>
                    <TurnCard
                      turn={{
                        id: h.key,
                        input: h.input,
                        attachments: [],
                        events: load.events,
                        status: "done",
                        error: null,
                        approval: null,
                      }}
                      turnSeq={idx}
                      initialEventView={eventView}
                      onViewChange={setEventView}
                      threadId={thread?.thread_id ?? null}
                      onDownloadArtifact={handleDownloadArtifact}
                      rate={rate}
                      onDecide={() => {}}
                      deciding={false}
                      onExport={handleExport}
                      exporting={exportingId === h.key}
                      isSystemAdmin={isSystemAdmin}
                      readOnly
                      loadState={load.state}
                      fallbackAnswer={h.fallbackAnswer}
                    />
                  </div>
                );
              })}
              <div style={{ /* 原「以下为本次新消息」分隔线样式,原样保留 */ }}>
                {t("playground.history_divider")}
              </div>
            </div>
          ) : (
            history.length > 0 && (
              /* 现有扁平文本历史块,原样保留(降级路径) */
              <div data-testid="playground-history"> ... </div>
            )
          )}
```

> 分隔线块在两分支都要有(懒分支末尾 + 扁平分支末尾),复用同一段 JSX/样式;实现者抽一个小的 `HistoryDivider` 局部组件避免重复,或原样复制该 `<div>`(与现有一致即可)。

- [ ] **Step 4: 跑测试 + typecheck + 全量**

Run: `cd apps/admin-ui && pnpm typecheck && npx vitest run src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx`
Expected: 新 3 条 PASS,原有全绿,typecheck 0 报错。

- [ ] **Step 5: 全量前端测试**

Run: `cd apps/admin-ui && npx vitest run`
Expected: 全绿。

- [ ] **Step 6: Commit**

```bash
git add apps/admin-ui/src/pages/agent_detail/PlaygroundTab.tsx apps/admin-ui/src/pages/agent_detail/__tests__/PlaygroundTab.test.tsx
git commit -m "feat(playground): 历史轮懒重建接线(IntersectionObserver 回放 + 计数守卫降级)"
```

---

## 收尾

全部任务后:
- 后端 `cd services/control-plane && uv run pytest && uv run mypy src/ && uv run ruff check`。
- 前端 `cd apps/admin-ui && pnpm typecheck && npx vitest run`。
- 终门 opus 全支评审(subagent-driven-development 流程),再走 finishing-a-development-branch。
- 人工冒烟:resume 一个多轮 thread,滚动确认历史轮懒填充出完整调试面板;构造/找一个含审批的 thread 确认降级回扁平文本;断网/坏 run 确认 fallback。
