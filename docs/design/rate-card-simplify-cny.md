# 模型定价简化 + 人民币化(Rate Card → Model Pricing)

> 状态:已定稿 · 分支 `billing/rate-card-simplify-cny`
> 背景:用户对「计价卡」页提了 5 点改进。本文把它们落成一致的范围。

## 1. 背景与现状

平台费率卡(`model_rate_card`,Stream Y3)当前模型:

- 自然键 `(tenant_id, provider, model, plan_tier, effective_from)`,平台行 `tenant_id=NULL`。
- **套餐分层** `plan_tier`、**时间版本** `effective_from`/`effective_until`、**平台加价** `markup_bps`。
- **价格单位**:整数 `*_token_micros` = micro-货币 / **token**(系数:数值 == 货币/百万token,见 SDK 注释)。粒度 = 1 货币/百万token,**填不了小数**。
- 厂商/模型创建表单**手输**,打错 → 计量静默 `unpriced`。

关键架构:**计量/定价分离 + 幂等重算**。`token_usage` 只记 token 数,成本由月度 rollup 现算(`token × 当前费率`)、重算整月、**无关账锁**。`temporal` 是「改价不污染历史」的唯一机制。加价链路:rollup `billed = base × (1+markup)`,ledger 存 base/markup/billed,`billing_admin` 把 markup 当平台毛利,`usage` 只给租户看 billed。

## 2. 决策

| # | 诉求 | 决策 |
|---|------|------|
| 1 | 名字不好 | 显示名 → **「模型定价」**(en: Model Pricing)。URL `/settings/rate-card` 不变 |
| 2 | 加价应在租户身上 | **本 PR 去掉 `markup_bps`**(rate_card 变纯成本价);**租户级加价另开 PR** |
| 3 | 缓存计价字段 | **保留**(默认 0) |
| 4 | 统一人民币、micro 难懂 | UI 单位 **「元 / 百万 tokens」**,**支持小数**;符号 ¥ |
| 4 | 砍套餐 + 时间(B1) | 砍 `plan_tier` + `effective_from` + `effective_until` |
| 5 | 一模型配一次 | 唯一键 `(tenant_id, provider, model)`,改价 = 编辑 |

**B1 取舍(已确认)**:砍 temporal 后,改价会让当月已产生用量按新价重算。平台无真发票(M2 未做)→ 仅报表数字浮动,可接受。

**加价过渡(已确认拆分)**:本 PR 删 `markup_bps` 后,rollup `billed = base`(加价暂 0,平台毛利暂 0),直到租户级加价 PR 上线。无真账单,过渡无影响。

## 3. 价格单位与精度(第 4 点核心)

- **存储**:rate_card 4 个价格字段重定义为 **micro-元 / 百万 tokens**(整数,无浮点)。
  字段重命名 `*_token_micros` → `*_per_mtok_micros`(避免旧"per token"语义误导)。
- **换算**:UI 填 `P` 元/百万tokens(可小数)↔ 存储 `round(P × 1_000_000)`。例 ¥0.5 → `500000`,¥0.1 → `100000`。粒度 ¥1e-6/百万token。
- **rollup**:`base_micros(micro元) = tokens × price_per_mtok_micros // 1_000_000`(整数 floor,与 `apply_markup` 现有 floor 一致)。
  校验:1000 token × ¥0.5/百万 = `1000 × 500000 // 1e6 = 500` micro元 = ¥0.0005 ✓。
- **ledger 不变**:`*_cost_micros` 仍是 micro-元 绝对额,语义/精度不动。
- **数据假设**:假设无生产 rate_card 数据(dev 阶段)。若有旧行,其值语义从"micro元/token"变,需 ×1e6 迁移 —— migration 注明,downgrade 不可逆。

## 4. 改动清单(分层)

### 4.1 protocol(`billing.py`)
- `ModelRateCardRecord/Upsert/Patch`:删 `plan_tier`、`effective_from`、`effective_until`、`markup_bps` 及 validator;价格字段重命名 `*_per_mtok_micros`。
- 删 `apply_markup`?→ **保留**(租户级 PR 复用),但本 PR rollup 传 markup=0 不调用。docstring 单位改人民币。

### 4.2 持久层
- **migration(新)**:drop `plan_tier`/`effective_from`/`effective_until`/`markup_bps` + 对应 CHECK;rename 4 价格列;drop 旧唯一索引,建 `(COALESCE(tenant_id,'0..0'), provider, model)`。downgrade 加回列(不可逆,注明)。
- `rate_card.py` store:`create`/`list`/`resolve` 删 `plan_tier`/`at`/`include_expired`;`_resolve` → 按 (provider, model) 取唯一行;`patch` 删 `effective_until`。

### 4.3 rollup(`job.py`)
- `resolve(...)` 删 `plan_tier`/`at` 实参。
- `base_micros` 计算加 `// 1_000_000`(新单位)。
- `billed = base`(markup 暂 0);`markup_micros = 0`。ledger 仍写 base/markup/billed 三件套(markup=0)。
- Z-2 metric 描述改人民币。

### 4.4 control-plane(`api/rate_card.py`)
- `list` 删 `include_expired`;`_price_details` 去 plan/effective/markup;409 文案「该厂商/模型已配置定价」。

### 4.5 admin-ui(`SettingsRateCard.tsx` + `api/rate_card.ts` + i18n)
- 厂商下拉(`fetchModelCatalog` providers)+ 模型联动下拉(models),替代 Input。
- 删:plan/effective/expired/markup 的列/filter/字段/DatePicker/validator/编辑只读项。
- 价格输入「元/百万tokens」支持小数(InputNumber step/precision),提交 ×1e6;展示 ÷1e6 + ¥。删 `microsPerTokenToUsdPerMillion`。
- i18n 双文件重写;路径不变。

## 5. 另开 PR — 租户级加价(**推迟到 M1**)
**租户级加价**:per-tenant `markup_bps` 配置(表/字段 + API + admin-ui 租户页)+ rollup 改用 `base × (1 + tenant.markup)`。复用现有 `apply_markup`、ledger base/markup/billed 三件套。

> 状态(2026-06-23 用户拍板):**推迟到 M1**,不在当前 sprint 做。过渡期 rollup `billed = base`(平台毛利 0)。`apply_markup` 已保留待复用。

## 6. 测试计划
- protocol:字段/校验/重命名单测。
- store:`_resolve` 单行 + 唯一冲突。
- migration:integration 真 PG 升/降 + 唯一约束。
- job:resolve 签名 + 新单位换算(// 1e6)+ billed=base 回归。
- control-plane:CRUD + 409 + 审计。
- admin-ui:vitest 下拉/小数提交;Playwright a11y(下拉 aria-label)。
