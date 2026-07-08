# 批量将平台技能归入一个分类 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有平台技能批量通道上加一个 `set_category` 动作,一次把选中的(或全部匹配的)平台技能归入同一分类,或清空分类。

**Architecture:** 对称扩展现有 `POST /v1/platform/skills/batch` 链路——store 层 `bulk_update_platform_skills` 加分类分支、API handler 加 `set_category` 字段与归一、前端批量工具条加"设分类 ▾"下拉。复用现有勾选/全部匹配选择器与 audit 单行。零迁移(复用 `skill.category` 列)。

**Tech Stack:** FastAPI + SQLAlchemy async + PostgreSQL(RLS)后端;React + TypeScript + Ant Design v5 前端;pytest / vitest 测试。

## Global Constraints

- 仅平台技能:所有写入限 `tenant_id IS NULL`,操作在 `bypass_rls_session()` 内,system_admin gated。
- 分类三态语义(与单条 PATCH `platform_skills.py:924` 一致):wire 层 `set_category` —— `None`/缺省=不动,`""`(空串)=清空(→NULL),非空串=设值;归一 `.strip() or None`。
- **清空走空串 `""`,不是 JSON `null`**(null→Python None→被当作"不动")。
- store 层用**显式 flag** 传三态:`update_category: bool` + `new_category: str | None`(增量拼 values 字典时 `None` 已被"清空"占用,不能再表示"跳过")。
- 只改父 `SkillRow.category`,**不碰 `skill_version`**(与 `set_platform_category` sql.py:1184 一致)。
- category 长度上限 64(API/Pydantic 层,`max_length=64`,与单条 PATCH 一致)。
- audit 维持**单条** `resource_id="batch"` 行,action 仍 `SKILL_STATUS_CHANGE`(批量通用标记),`details` 增 `set_category`。不发逐行 `SKILL_CATEGORY_CHANGED`。
- 不可变更新:memory store 用 `model_copy(update=...)`,勿原地改。
- 提交信息用 conventional commits(`feat:`),全局已禁用 attribution,**不加 Co-Authored-By 尾注**。分支 `feat/batch-skill-category` 已建、spec 已提交在其上。

---

### Task 1: Store ABC 签名 + memory 实现 + memory 测试

给抽象基类加参、内存实现加分类分支、内存测试覆盖设值/清空/仅分类/不动。纯内存,秒级红绿。

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/skill/base.py:613-630`(ABC 签名 + docstring)
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/skill/memory.py:723-756`(内存实现)
- Test: `packages/expert-work-persistence/tests/test_in_memory_skill_store.py`(追加测试,置于既有 `test_bulk_update_by_ids_sets_pinned` 附近 ~517)

**Interfaces:**
- Produces:`bulk_update_platform_skills(*, ids=None, filter_status=None, filter_category=None, filter_q=None, set_status=None, set_pinned=None, update_category: bool = False, new_category: str | None = None) -> int` —— Task 2、3 依赖此新签名。

- [ ] **Step 1: 写失败测试**

追加到 `test_in_memory_skill_store.py`(沿用文件内 `_seed_platform`、`InMemorySkillStore`、`uuid4`、`@pytest.mark.asyncio`):

```python
@pytest.mark.asyncio
async def test_bulk_set_category_by_ids() -> None:
    store = InMemorySkillStore()
    a = await _seed_platform(store, "a")
    b = await _seed_platform(store, "b")
    c = await _seed_platform(store, "c", category="old")
    n = await store.bulk_update_platform_skills(ids=[a, b], update_category=True, new_category="研发")
    assert n == 2
    rows, _ = await store.list_platform_skills()
    by_id = {s.id: s.category for s in rows}
    assert by_id[a] == "研发" and by_id[b] == "研发"
    assert by_id[c] == "old"  # untouched


@pytest.mark.asyncio
async def test_bulk_clear_category_writes_none() -> None:
    store = InMemorySkillStore()
    a = await _seed_platform(store, "a", category="研发")
    n = await store.bulk_update_platform_skills(ids=[a], update_category=True, new_category=None)
    assert n == 1
    rows, _ = await store.list_platform_skills()
    assert rows[0].category is None


@pytest.mark.asyncio
async def test_bulk_category_only_needs_no_status_or_pinned() -> None:
    # update_category alone satisfies the "at least one action" guard.
    store = InMemorySkillStore()
    a = await _seed_platform(store, "a")
    n = await store.bulk_update_platform_skills(ids=[a], update_category=True, new_category="设计")
    assert n == 1


@pytest.mark.asyncio
async def test_bulk_no_action_raises() -> None:
    store = InMemorySkillStore()
    a = await _seed_platform(store, "a")
    with pytest.raises(ValueError):
        await store.bulk_update_platform_skills(ids=[a])  # no set_* and update_category=False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/expert-work-persistence && python -m pytest tests/test_in_memory_skill_store.py -k "bulk_set_category or bulk_clear_category or bulk_category_only or bulk_no_action" -v`
Expected: FAIL —— `bulk_update_platform_skills() got an unexpected keyword argument 'update_category'`。

- [ ] **Step 3: 改 ABC 签名(base.py:613)**

把 `base.py` 的抽象方法签名与 docstring 改成:

```python
    @abc.abstractmethod
    async def bulk_update_platform_skills(
        self,
        *,
        ids: Sequence[UUID] | None = None,
        filter_status: SkillStatus | None = None,
        filter_category: str | None = None,
        filter_q: str | None = None,
        set_status: SkillStatus | None = None,
        set_pinned: bool | None = None,
        update_category: bool = False,
        new_category: str | None = None,
    ) -> int:
        """Atomically patch many platform skills; returns the affected count.

        Exactly one selector: ``ids`` (an explicit list) OR the
        ``filter_*`` predicate (every NULL-tenant skill matching it — the
        "select all N matching" path). At least one action of ``set_status`` /
        ``set_pinned`` / ``update_category`` must be given. ``state_changed_at``
        is bumped only when ``set_status`` is provided. When ``update_category``
        is True the category column is written to ``new_category`` (``None``
        clears it); only the parent ``skill`` row is touched, never
        ``skill_version``. Caller MUST be inside ``bypass_rls_session()``.
        """
```

- [ ] **Step 4: 改内存实现(memory.py:723)**

签名加同样两参;校验行与 values 拼装加分类分支:

```python
        if set_status is None and set_pinned is None and not update_category:
            raise ValueError(
                "bulk_update_platform_skills requires set_status, set_pinned, or update_category"
            )
```

在 `if set_pinned is not None: update["pinned"] = set_pinned` 之后追加:

```python
        if update_category:
            update["category"] = new_category
```

（其余逻辑不变；仍 `row.model_copy(update=update)` 不可变更新。）

- [ ] **Step 5: 跑测试确认通过**

Run: `cd packages/expert-work-persistence && python -m pytest tests/test_in_memory_skill_store.py -v`
Expected: PASS(新 4 条 + 既有全绿)。

- [ ] **Step 6: 提交**

```bash
git add packages/expert-work-persistence/src/expert_work/persistence/skill/base.py \
        packages/expert-work-persistence/src/expert_work/persistence/skill/memory.py \
        packages/expert-work-persistence/tests/test_in_memory_skill_store.py
git commit -m "feat(skill-store): bulk_update_platform_skills 支持批量设/清分类(ABC+memory)"
```

---

### Task 2: Store SQL 实现 + 真库测试

SQL 实现加分类分支,真 PG 验证只改 `skill`、不改 `skill_version`。

**Files:**
- Modify: `packages/expert-work-persistence/src/expert_work/persistence/skill/sql.py:1010-1045`
- Test: `packages/expert-work-persistence/tests/test_sql_skill_evolution_store.py`(追加,沿用 `skill_store` fixture 与 `bypass_rls_session`)

**Interfaces:**
- Consumes:Task 1 定义的新签名。
- Produces:SQL 侧同签名实现,Task 3 通过 store 抽象调用。

- [ ] **Step 1: 写失败测试**

追加到 `test_sql_skill_evolution_store.py`(该文件 fixture 为 `skill_store: tuple[SqlSkillStore, AsyncEngine]`,平台操作需包在 `bypass_rls_session()` 内 —— import 已有则复用,否则从 `expert_work.persistence.session import bypass_rls_session`):

```python
@pytest.mark.asyncio
async def test_bulk_set_and_clear_category_real_pg(
    skill_store: tuple[SqlSkillStore, AsyncEngine],
) -> None:
    store, _engine = skill_store
    async with bypass_rls_session():
        a = await store.create_platform_skill(skill_id=uuid4(), name="a")
        b = await store.create_platform_skill(skill_id=uuid4(), name="b", category="old")
        # set on ids
        n = await store.bulk_update_platform_skills(
            ids=[a.id], update_category=True, new_category="研发"
        )
        assert n == 1
        # clear
        m = await store.bulk_update_platform_skills(
            ids=[b.id], update_category=True, new_category=None
        )
        assert m == 1
        got_a = await store.get_platform_skill(skill_id=a.id)
        got_b = await store.get_platform_skill(skill_id=b.id)
        assert got_a is not None and got_a.category == "研发"
        assert got_b is not None and got_b.category is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/expert-work-persistence && python -m pytest tests/test_sql_skill_evolution_store.py -k bulk_set_and_clear_category -v`
Expected: FAIL —— `unexpected keyword argument 'update_category'`。(若本机无测试 PG,该套件按既有约定会 skip;此时在有 PG 的 CI 上验证。)

- [ ] **Step 3: 改 SQL 实现(sql.py:1010)**

签名加 `update_category: bool = False, new_category: str | None = None`。校验行改:

```python
        if set_status is None and set_pinned is None and not update_category:
            raise ValueError(
                "bulk_update_platform_skills requires set_status, set_pinned, or update_category"
            )
```

在 `if set_pinned is not None: values["pinned"] = set_pinned` 之后追加:

```python
        if update_category:
            values["category"] = new_category
```

（`UPDATE(SkillRow)` 目标与 where 不变——只改 `skill` 表。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/expert-work-persistence && python -m pytest tests/test_sql_skill_evolution_store.py -k bulk_set_and_clear_category -v`
Expected: PASS(有 PG 时)。

- [ ] **Step 5: 提交**

```bash
git add packages/expert-work-persistence/src/expert_work/persistence/skill/sql.py \
        packages/expert-work-persistence/tests/test_sql_skill_evolution_store.py
git commit -m "feat(skill-store): bulk_update_platform_skills SQL 分类分支(仅改 skill 行)"
```

---

### Task 3: API —— `set_category` 字段 + handler 归一 + audit

批量 body 加字段,handler 校验/归一/调 store/写 audit。API 测试用内存 store(快)。

**Files:**
- Modify: `services/control-plane/src/control_plane/api/platform_skills.py:214-225`(body)、`:985-1036`(handler)
- Test: `services/control-plane/tests/test_platform_skills_api.py`

**Interfaces:**
- Consumes:Task 1 store 新签名。
- Produces:`POST /v1/platform/skills/batch` 接受 `set_category`。

- [ ] **Step 1: 写失败测试**

追加到 `test_platform_skills_api.py`(沿用文件既有 client/system_admin fixture 与"batch"用例风格)。写三条:设值、清空(空串)、仅分类无其它动作:

```python
@pytest.mark.asyncio
async def test_batch_set_category_by_ids(admin_client, seed_two_platform_skills) -> None:
    a, b = seed_two_platform_skills
    resp = await admin_client.post(
        "/v1/platform/skills/batch",
        json={"ids": [str(a), str(b)], "set_category": "研发"},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2
    got = await admin_client.get(f"/v1/platform/skills/{a}")
    assert got.json()["category"] == "研发"


@pytest.mark.asyncio
async def test_batch_clear_category_with_empty_string(admin_client, seed_two_platform_skills) -> None:
    a, _ = seed_two_platform_skills  # a seeded with a category
    resp = await admin_client.post(
        "/v1/platform/skills/batch",
        json={"ids": [str(a)], "set_category": ""},
    )
    assert resp.status_code == 200
    got = await admin_client.get(f"/v1/platform/skills/{a}")
    assert got.json()["category"] is None


@pytest.mark.asyncio
async def test_batch_category_only_passes_action_guard(admin_client, seed_two_platform_skills) -> None:
    a, _ = seed_two_platform_skills
    resp = await admin_client.post(
        "/v1/platform/skills/batch",
        json={"ids": [str(a)], "set_category": "设计"},
    )
    assert resp.status_code == 200  # not 422 — category counts as an action


@pytest.mark.asyncio
async def test_batch_no_action_422(admin_client, seed_two_platform_skills) -> None:
    a, _ = seed_two_platform_skills
    resp = await admin_client.post(
        "/v1/platform/skills/batch",
        json={"ids": [str(a)]},  # no set_status / set_pinned / set_category
    )
    assert resp.status_code == 422
```

> 注:`admin_client` / `seed_two_platform_skills` 用文件里既有等价 fixture;若命名不同,按现有 batch 测试(`test_platform_skills_api.py` 内既有 `/batch` 用例)复用同款。其中一条 seed 需带初始 category 以验证清空。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd services/control-plane && python -m pytest tests/test_platform_skills_api.py -k "batch_set_category or batch_clear_category or batch_category_only or batch_no_action" -v`
Expected: FAIL —— 设值/清空断言不符(字段被忽略),且 category-only 现返回 422。

- [ ] **Step 3: body 加字段(platform_skills.py:214)**

`_BatchPlatformSkillsBody` 内 `filter` 字段前后任意处加:

```python
    set_category: str | None = Field(default=None, max_length=64)
```

- [ ] **Step 4: 改 handler(platform_skills.py:985)**

校验行(现 `:999`)扩成:

```python
        if body.set_status is None and body.set_pinned is None and body.set_category is None:
            raise HTTPException(
                status_code=422,
                detail="batch body must set at least one of: set_status, set_pinned, set_category",
            )
```

在 `has_ids`/`has_filter` 校验之后、`store.bulk_update_platform_skills(...)` 调用之前,归一分类一次:

```python
        update_category = body.set_category is not None
        new_category = (body.set_category.strip() or None) if update_category else None
```

store 调用(现 `:1011`)加两参:

```python
            updated = await store.bulk_update_platform_skills(
                ids=body.ids,
                filter_status=body.filter.status if body.filter else None,
                filter_category=body.filter.category if body.filter else None,
                filter_q=body.filter.q if body.filter else None,
                set_status=body.set_status,
                set_pinned=body.set_pinned,
                update_category=update_category,
                new_category=new_category,
            )
```

audit `details`(现 `:1028`)加一行:

```python
            details={
                "scope": "platform",
                "mode": "ids" if has_ids else "filter",
                "updated": updated,
                "set_status": body.set_status.value if body.set_status else None,
                "set_pinned": body.set_pinned,
                "set_category": new_category if update_category else None,
            },
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd services/control-plane && python -m pytest tests/test_platform_skills_api.py -k batch -v`
Expected: PASS(新 4 条 + 既有 batch 用例不回归)。

- [ ] **Step 6: 提交**

```bash
git add services/control-plane/src/control_plane/api/platform_skills.py \
        services/control-plane/tests/test_platform_skills_api.py
git commit -m "feat(platform-skills-api): POST /batch 支持 set_category(设值/空串清空)"
```

---

### Task 4: 前端 —— SDK 类型 + 工具条"设分类 ▾"下拉 + i18n

SDK 类型加字段,批量工具条加下拉(现有分类 + 清空),组件测试验证提交体。

**Files:**
- Modify: `apps/admin-ui/src/api/platform-skills.ts:299-304`(`BulkUpdatePlatformSkillsBody`)
- Modify: `apps/admin-ui/src/pages/SettingsPlatformSkills.tsx`(antd import、`runBatch` 类型 `:394`、工具条 `:809`)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts`、`apps/admin-ui/src/i18n/locales/en.ts`(platform_skills 段加两键)
- Test: `apps/admin-ui/src/pages/__tests__/SettingsPlatformSkills.test.tsx`

**Interfaces:**
- Consumes:Task 3 的 `POST /batch` `set_category`。

- [ ] **Step 1: 写失败测试**

追加到 `SettingsPlatformSkills.test.tsx`(镜像既有 `batch-locks the selected skills…` 用例:装 adapter、抓 `batched`、用 testid 驱动)。前置:`/categories` mock 需返回至少含 `"研发"` 的列表,使下拉有项。

```tsx
it("batch-sets a category on the selected skills via the batch endpoint", async () => {
  const batched: Array<Record<string, unknown>> = [];
  installAdapter([
    ...listAndCategoryHandlers, // 复用本文件既有 list + /categories(含 "研发")handler 集
    {
      match: (u, m) => u.endsWith("/platform/skills/batch") && m === "post",
      respond: ({ data }) => {
        batched.push(typeof data === "string" ? JSON.parse(data) : (data as object));
        return { updated: 2 };
      },
    },
  ]);
  renderPage(); // 复用本文件既有 system_admin 渲染helper
  await waitFor(() => expect(screen.getByTestId("ps-batch-toolbar")).toBeInTheDocument());
  await userEvent.click(screen.getByTestId("ps-batch-set-category"));
  await userEvent.click(await screen.findByText("研发"));
  await waitFor(() => expect(batched.length).toBe(1));
  expect(batched[0].set_category).toBe("研发");
  expect(batched[0].ids).toEqual(["psk-1", "psk-2"]);
});

it("batch clear-category posts set_category as empty string", async () => {
  const batched: Array<Record<string, unknown>> = [];
  installAdapter([
    ...listAndCategoryHandlers,
    {
      match: (u, m) => u.endsWith("/platform/skills/batch") && m === "post",
      respond: ({ data }) => {
        batched.push(typeof data === "string" ? JSON.parse(data) : (data as object));
        return { updated: 2 };
      },
    },
  ]);
  renderPage();
  await waitFor(() => expect(screen.getByTestId("ps-batch-toolbar")).toBeInTheDocument());
  await userEvent.click(screen.getByTestId("ps-batch-set-category"));
  // "清空分类" 菜单项(英文测试环境下文案见 en.ts: "Clear category")
  await userEvent.click(await screen.findByText("Clear category"));
  await waitFor(() => expect(batched.length).toBe(1));
  expect(batched[0].set_category).toBe("");
});
```

> 若本文件测试跑在 en locale,菜单文案用 `"Set category"` / `"Clear category"`;若 zh,则 `"设分类"` / `"清空分类"`。按文件顶部 `import "../../i18n"` 的默认语言取对应文案(与既有用例 `expect(...).not.toContain("platform_skills")` 判定同源)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/admin-ui && npx vitest run src/pages/__tests__/SettingsPlatformSkills.test.tsx -t "category"`
Expected: FAIL —— 找不到 `ps-batch-set-category`。

- [ ] **Step 3: SDK 类型加字段(platform-skills.ts:299)**

`BulkUpdatePlatformSkillsBody` 加(`""` 清空、分类名设值、省略不动):

```ts
export interface BulkUpdatePlatformSkillsBody {
  set_status?: PlatformSkillStatus;
  set_pinned?: boolean;
  set_category?: string;
  ids?: string[];
  filter?: BulkPlatformSkillsFilter;
}
```

- [ ] **Step 4: 拓宽 runBatch 类型 + 加下拉(SettingsPlatformSkills.tsx)**

4a. 顶部 antd 值导入里加 `Dropdown`(该文件从 `"antd"` 具名导入 Button/Space/Select 等,加进同一 import)。

4b. `runBatch` 类型(`:394`)拓宽:

```tsx
    async (patch: Pick<BulkUpdatePlatformSkillsBody, "set_status" | "set_pinned" | "set_category">) => {
```

（函数体不改:`{ ...patch, ids }` / `{ ...patch, filter }` 已透传 `set_category`。）

4c. 组件顶部(其它 const 附近)加清空哨兵 key:

```tsx
const CLEAR_CATEGORY_KEY = "__ps_clear_category__";
```

4d. 工具条内(`:852` "全部匹配"复选框之前)插入下拉:

```tsx
              <Dropdown
                trigger={["click"]}
                disabled={batchBusy}
                menu={{
                  items: [
                    ...categories.map((c) => ({ key: c, label: c })),
                    { type: "divider" as const },
                    { key: CLEAR_CATEGORY_KEY, label: t("platform_skills.batch_clear_category") },
                  ],
                  onClick: ({ key }) =>
                    void runBatch({ set_category: key === CLEAR_CATEGORY_KEY ? "" : key }),
                }}
              >
                <Button size="small" loading={batchBusy} data-testid="ps-batch-set-category">
                  {t("platform_skills.batch_set_category")}
                </Button>
              </Dropdown>
```

- [ ] **Step 5: 加 i18n 键**

`zh-CN.ts` 的 `platform_skills` 段加:

```ts
    batch_set_category: "设分类",
    batch_clear_category: "清空分类",
```

`en.ts` 的 `platform_skills` 段加:

```ts
    batch_set_category: "Set category",
    batch_clear_category: "Clear category",
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd apps/admin-ui && npx vitest run src/pages/__tests__/SettingsPlatformSkills.test.tsx`
Expected: PASS(新 2 条 + 既有全绿)。

- [ ] **Step 7: 类型检查 + 提交**

Run: `cd apps/admin-ui && npx tsc --noEmit`
Expected: 无报错。

```bash
git add apps/admin-ui/src/api/platform-skills.ts \
        apps/admin-ui/src/pages/SettingsPlatformSkills.tsx \
        apps/admin-ui/src/i18n/locales/zh-CN.ts \
        apps/admin-ui/src/i18n/locales/en.ts \
        apps/admin-ui/src/pages/__tests__/SettingsPlatformSkills.test.tsx
git commit -m "feat(platform-skills-ui): 批量工具条加\"设分类\"下拉(设值/清空)"
```

---

## Self-Review 对照

- **Spec 覆盖**:store 三态(Task 1/2)、API 字段+归一+audit(Task 3)、前端下拉+SDK+i18n(Task 4)、清空空串语义(Task 3 Step1 + Task 4 Step1)、仅 SkillRow(Task 2 Step1 断言)、平台限定(Global Constraints + 复用 `bypass_rls_session`)、下拉仅已有分类(Task 4 4d `categories.map`)—— 全部有对应任务。RLS 不越租户由既有 `test_rls_skill_platform.py` 覆盖,现有链路未改 where,故不新增 RLS 测试。
- **占位符**:无 TBD/TODO;每步有可运行代码/命令与期望输出。
- **类型一致**:`update_category`/`new_category` 三层签名一致;`set_category`(wire)贯穿 body→handler→SDK→下拉;`CLEAR_CATEGORY_KEY` 单一定义。
