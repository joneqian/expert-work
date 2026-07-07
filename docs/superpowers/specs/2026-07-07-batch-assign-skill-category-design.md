# 批量将平台技能归入一个分类 — 设计文档

- **日期**: 2026-07-07
- **状态**: 已批准设计,待实施计划
- **范围**: 仅平台技能库(`tenant_id IS NULL`,system_admin 管理面)

## 1. 背景与目标

平台技能列表页(`SettingsPlatformSkills.tsx`)已有勾选 + 批量工具条,支持批量锁定/解锁、归档/激活,以及"应用到全部 N 个匹配项"。但**分类只能逐条编辑**(技能详情页),批量给一批技能设同一分类需要一条条点,运营在冷启动整理资产库时很痛。

目标:在**现有批量通道**上加一个"设分类"动作,一次把选中的(或全部匹配的)平台技能归入同一分类,或清空分类。

这是对已有 batch 链路的**对称扩展**,不引入新机器。

## 2. 已锁定的决策

1. **分类来源:仅限已有分类下拉。** 批量动作只能从现有分类(种子 + 库内已存在)里选,**不能在批量里自由新建分类**——防止一次批量制造出成规模的错别字分类。新建分类仍走单条详情页编辑。
2. **支持"清空分类"**(设为无分类),与单条 PATCH 的 blank→NULL 对称。
3. **仅平台技能。** 租户技能 SDK 无分类 patch,本次不动租户侧。

## 3. 现状(扩展所依赖的接缝)

分类存储:`skill.category` 是**可空 `Text` 列**——非外键、非枚举、无关联表、单分类。"分类集合" = `SELECT DISTINCT category` + 硬编码种子 `SEED_PLATFORM_CATEGORIES`(`platform_skills.py:111`)。

关键既有代码:

| 层 | 位置 | 现状 |
|---|---|---|
| 单条 PATCH body | `platform_skills.py:192` `_PatchPlatformSkillBody` | 有 `category: str \| None`(max 64) |
| 单条 PATCH 语义 | `platform_skills.py:924` | `category is not None` → 应用;`.strip() or None` → 空串清空 |
| 批量 body | `platform_skills.py:214` `_BatchPlatformSkillsBody` | 仅 `set_status`/`set_pinned` + `ids` XOR `filter` |
| 批量 handler | `platform_skills.py:985` `batch_update_platform_skills` | 校验"至少一个动作";写**单条** audit(`resource_id="batch"`,无逐行刷屏) |
| 单条分类写入 | store `set_platform_category`(ABC `base.py:694` / `sql.py:1178` / `memory.py:830`) | **只更新 `SkillRow`,不碰 `skill_version`**;`.strip() or None` 归一 |
| 批量 store | `bulk_update_platform_skills`(ABC `base.py:613` / `sql.py:1010` / `memory.py:723`) | 增量拼 `values` 字典;仅 `status`/`pinned` |
| 分类列表 | `GET /categories`(`platform_skills.py:952`)→ 种子 + distinct | 前端已加载入 `categories` state |
| 前端批量工具条 | `SettingsPlatformSkills.tsx:809` `ps-batch-toolbar` | 锁/归档按钮 + "应用到全部 N 项"复选框(`:853`);`runBatch`(`:393`) |
| 前端 SDK | `platform-skills.ts:299` `BulkUpdatePlatformSkillsBody` | 仅 `set_status`/`set_pinned`/`ids`/`filter` |

## 4. 设计

### 4.1 三态问题(核心,勿踩)

分类批量有三种意图,`None` 一个值表达不了:

- **不动分类**(只批量改状态/pin)
- **设为某分类** `"研发"`
- **清空分类**(设为 NULL)

单条 PATCH 用**空串区分**:`category=None`→跳过,`category=""`→清空,`category="研发"`→设值(`:924-925`)。批量在 **API/wire 层沿用同一约定**;但 store 层 `bulk_update_platform_skills` 增量拼 `values` 字典,`None` 已被"清空"占用,**不能**再用 `None` 表示"跳过"。故 **store 层用显式布尔 flag**。

### 4.2 后端

**Body**(`_BatchPlatformSkillsBody`,`platform_skills.py:214`)新增:
```python
set_category: str | None = Field(default=None, max_length=64)
```
沿用单条 PATCH 的 max=64 与空串语义:`None`=不动,`""`=清空,非空串=设值。(注:`_BatchFilter.category` 是"按分类筛选",max 128,与本字段无关,保持不动。)

**Handler**(`batch_update_platform_skills`,`:985`):
- 校验行(`:999`)扩成:`set_status is None and set_pinned is None and set_category is None` → 422。因 `""`(清空)`is not None`,清空动作会被正确计为"有动作"。
- store 调用(`:1011`)新增两参:
  ```python
  update_category=body.set_category is not None,
  new_category=(body.set_category.strip() or None) if body.set_category is not None else None,
  ```
  归一在 handler 做(与单条 `:925` 一致),store 直接写。
- audit(`:1019`):**维持单条 audit 行**(既有"无逐行刷屏"原则),action 仍 `SKILL_STATUS_CHANGE`(此处是"批量变更"的通用标记),`details` 增 `"set_category": <归一后值或 None>`。
  - *决策说明*:批量可同时改状态+分类,单条 audit 无法用一个精确 action 覆盖全部,故沿用既有通用 action + 明细字段的做法,不为分类单独发行 `SKILL_CATEGORY_CHANGED`。这是与既有 batch 行为一致的刻意选择,非遗漏。

**Store**(三处签名对齐)`bulk_update_platform_skills` 新增参数:
```python
update_category: bool = False,
new_category: str | None = None,
```
- **ABC**(`base.py:613`):加参 + docstring 说明三态与"仅更新 `SkillRow`"。
- **SQL**(`sql.py:1010`):校验"至少一动作"扩入 `update_category`;`if update_category: values["category"] = new_category`。UPDATE 目标仍是 `SkillRow`(与 `set_platform_category:1184` 一致,**不动 `skill_version`**)。
- **Memory**(`memory.py:723`):同构,`if update_category: update["category"] = new_category`。

### 4.3 前端

**SDK**(`platform-skills.ts:299`)`BulkUpdatePlatformSkillsBody` 加:
```ts
set_category?: string   // "" = 清空;某分类名 = 设值;省略 = 不动
```
> 注意:清空走**空串 `""`**,不是 JSON `null`(`null`→Python `None`→被跳过)。

**工具条**(`SettingsPlatformSkills.tsx:809`)加 **"设分类 ▾"** antd `Dropdown`:
- 菜单项 = 现有 `categories` state(已由 `loadCategories` 加载)+ 末尾一项 **"清空分类"**。
- 点某分类 → 立即应用(单击即生效,与锁/归档按钮一致);点"清空分类" → 发 `set_category: ""`。
- 复用既有 **"应用到全部 N 项"** 复选框(`:853`)的 `ids` vs `filter` 选择器,无需新逻辑。
- `runBatch`(`:393`)类型从 `Pick<…,"set_status"|"set_pinned">` 拓宽含 `set_category`。

### 4.4 数据流

```
勾选技能(或勾"全部匹配") → 点"设分类▾"选分类
  → runBatch({ set_category, ids|filter })
  → POST /v1/platform/skills/batch
  → handler 校验 + 归一 → store.bulk_update_platform_skills(update_category=True, new_category=…)
  → UPDATE skill SET category=… WHERE tenant_id IS NULL AND (id IN … | filter)
  → {updated: N} → 前端刷新列表 + 提示"已归类 N 个"
```

## 5. 非目标(YAGNI)

- 不做多分类/标签(仍单分类一列)。
- 不做批量自由新建分类(仅下拉已有;新建走单条)。
- 不动租户技能。
- 不引入分类表/外键/枚举(维持自由文本模型)。
- 不做 `skill_version` 级分类批改(与单条编辑一致,只改父 `skill` 行)。
- server 端**不**强制"必须是已存在分类"(自由文本模型下代价高);由 UI 下拉约束,server 仅 length-cap 64。

## 6. 测试计划(TDD,先红后绿)

**Store 层**
- `packages/expert-work-persistence/tests/test_in_memory_skill_store.py`:memory `bulk_update_platform_skills` — `update_category=True` 设值 / 清空(`new_category=None`)/ 仅分类(不带 status/pinned)/ ids 与 filter 两条选择器 / `update_category=False` 时不碰 category。
- `packages/expert-work-persistence/tests/test_sql_skill_evolution_store.py`(或同级 SQL store 测试):SQL 侧同上关键用例,验证只改 `SkillRow`、不改 `skill_version`。
- `packages/expert-work-persistence/tests/test_rls_skill_platform.py`:确认批量分类仍限 `tenant_id IS NULL`(不越租户)。

**API 层** `services/control-plane/tests/test_platform_skills_api.py`
- `set_category` on `ids`;on `filter`;`""` 清空;分类 + 状态组合;仅 `set_category`(不带其它动作)通过校验;三个动作全 None → 422;`ids`/`filter` 二选一校验不变;audit `details.set_category` 正确;`null` 不触发分类更改(仅空串触发清空)。

**前端** `apps/admin-ui/src/pages/__tests__/SettingsPlatformSkills.test.tsx`
- "设分类▾"渲染现有分类 + "清空分类";点某项 → `bulkUpdatePlatformSkills` 收到对应 `set_category`;点"清空分类" → `set_category: ""`;"应用到全部 N 项"勾选下走 `filter` 选择器。

## 7. 改动文件清单

| 文件 | 改动 |
|---|---|
| `services/control-plane/src/control_plane/api/platform_skills.py` | body 加 `set_category`;handler 校验 + 归一 + store 调用 + audit details |
| `packages/expert-work-persistence/src/expert_work/persistence/skill/base.py` | ABC 加 `update_category`/`new_category` + docstring |
| `packages/expert-work-persistence/src/expert_work/persistence/skill/sql.py` | SQL bulk 加分类分支 |
| `packages/expert-work-persistence/src/expert_work/persistence/skill/memory.py` | memory bulk 加分类分支 |
| `apps/admin-ui/src/api/platform-skills.ts` | `BulkUpdatePlatformSkillsBody` 加 `set_category?` |
| `apps/admin-ui/src/pages/SettingsPlatformSkills.tsx` | 工具条加"设分类▾"Dropdown;`runBatch` 拓宽类型 |
| 上述 4 个测试文件 | 对应用例 |

无 DB 迁移(复用现有 `category` 列)。
