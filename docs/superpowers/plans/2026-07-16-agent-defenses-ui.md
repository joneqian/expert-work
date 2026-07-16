# Agent 防御守卫 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `DefenseSpec` 的全部防御开关暴露进 Agent 配置表单的可视化 Form 视图,运维无需手改 manifest YAML 即可配置一个 agent 的安全姿态。

**Architecture:** 纯前端增量。整个 Agent 配置是 manifest-YAML 存法(整份 spec 作为一个 JSONB blob 存 `agent_spec.spec_json`),`defenses` 早已端到端跑通 —— create/update API、Pydantic 校验、持久化全支持,表单只是从不生成 `defenses:` 那段 YAML。本计划只加 form_model 投影 + 一个 FormView "防御" section + ManifestEditor tab + i18n。**零后端/API/DB/protocol 改动。**

**Tech Stack:** React + TypeScript + antd(`Switch`/`Select`/`Alert`);现有 manifest-editor 框架(`specOf`/`patchSpec` 不可变投影);react-i18next(zh-CN + en);vitest + @testing-library/react。

**Spec:** `docs/superpowers/specs/2026-07-16-agent-defenses-ui-design.md`

## Global Constraints

- **零后端改动。** 若发现需要改 control-plane / protocol / persistence,停下上报 —— 说明设计前提被打破。
- **不可变更新。** 所有 `set*` 投影必须返回新 manifest 对象,不得原地改(镜像 `patchSpec`)。
- **默认值省略。** 一个开关等于其 `DefenseSpec` 默认值时,**不写进 YAML**;仅非默认值落 key。父开关关闭时,连带清掉其 `_on_error` 子键(不留孤儿)。`DefenseSpec` 默认值:`prompt_injection=spotlight`、`output_screen=block`、其余(`output_judge`/`output_dlp`/`action_screen`)全 `off`、`*_on_error=open`。
- **`form_model.ts` 被 `file(1)` 判为 `data`**(含非 UTF-8 多字节字符),裸 `grep` 会静默跳过 —— 需要 grep 时用 `grep -a`。**勿"修复"该文件编码。** 用 Read/Edit 工具正常操作即可。
- 所有面向用户文案走 i18n,同时提供 zh-CN + en,不硬编码中文串。
- 测试运行:`cd apps/admin-ui && pnpm exec vitest run <测试文件路径>`;类型检查:`cd apps/admin-ui && pnpm typecheck`(即 `tsc -b --noEmit`)。

---

## 文件结构

| 文件 | 职责 | 本计划动作 |
|---|---|---|
| `apps/admin-ui/src/components/manifest-editor/form_model.ts` | manifest ⇄ 表单控件的读写投影 | 加 `defenses?`/`extends?` 类型 + `patchDefenses` + 7×2 读写 + `readExtends`(Task 1) |
| `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts` | 投影单测 | 加 defenses 读写单测(Task 1) |
| `apps/admin-ui/src/components/manifest-editor/FormView.tsx` | 分组表单视图 | `FormSection` 加 `defenses` + 新 section JSX + 告警(Task 2) |
| `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx` | tab 壳 | `MANIFEST_TABS` 加 `defenses` tab(Task 2) |
| `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `en.ts` | 文案 | 新 i18n 键(Task 2) |
| `apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx` | section 组件测试 | 加 defenses 渲染/交互/联动测试(Task 2) |

拆两个任务:Task 1 = 纯逻辑投影(form_model 单测独立可验),Task 2 = UI 接线(消费 Task 1 的读写 API)。reviewer 可独立门控 Task 1 的默认省略/孤儿清理逻辑。

---

## Task 1: form_model 防御投影(读写 + 类型)

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/form_model.ts`(类型区 ~L49-118;`patchSpec` 后加 `patchDefenses`;文件尾追加读写)
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts`

**Interfaces:**
- Consumes: 现有 `specOf(m)`、`patchSpec(m, spec)`、`AgentManifest` 类型(同文件)。
- Produces(Task 2 消费):
  - `readPromptInjection(m): "spotlight" | "off"`,`setPromptInjection(m, v): AgentManifest`
  - `readOutputScreen(m): "block" | "off"`,`setOutputScreen(m, v): AgentManifest`
  - `readOutputJudge(m): "block" | "off"`,`setOutputJudge(m, v): AgentManifest`
  - `readOutputJudgeOnError(m): "open" | "closed"`,`setOutputJudgeOnError(m, v): AgentManifest`
  - `readActionScreen(m): "off" | "block" | "approval"`,`setActionScreen(m, v): AgentManifest`
  - `readActionScreenOnError(m): "open" | "closed"`,`setActionScreenOnError(m, v): AgentManifest`
  - `readOutputDlp(m): "redact" | "off"`,`setOutputDlp(m, v): AgentManifest`
  - `readExtends(m): string | undefined`

- [ ] **Step 1: 写失败测试**

追加到 `apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts`(顶部 import 区加下列符号,文件尾加两个 describe):

```ts
// —— 追加到顶部 import(与现有 "../form_model" import 合并)——
import {
  readPromptInjection,
  readOutputScreen,
  readOutputJudge,
  readOutputJudgeOnError,
  readActionScreen,
  readActionScreenOnError,
  readOutputDlp,
  readExtends,
  setPromptInjection,
  setOutputScreen,
  setOutputJudge,
  setOutputJudgeOnError,
  setActionScreen,
  setActionScreenOnError,
  setOutputDlp,
} from "../form_model";
import type { AgentManifest } from "../form_model";

// —— 追加到文件尾 ——
describe("form_model defenses readers (default-aware)", () => {
  it("reads effective DefenseSpec defaults when defenses absent", () => {
    const m: AgentManifest = { spec: {} };
    expect(readPromptInjection(m)).toBe("spotlight");
    expect(readOutputScreen(m)).toBe("block");
    expect(readOutputJudge(m)).toBe("off");
    expect(readOutputJudgeOnError(m)).toBe("open");
    expect(readActionScreen(m)).toBe("off");
    expect(readActionScreenOnError(m)).toBe("open");
    expect(readOutputDlp(m)).toBe("off");
    expect(readExtends(m)).toBeUndefined();
  });

  it("reads explicit values", () => {
    const m: AgentManifest = {
      spec: {
        extends: "secure-template",
        defenses: {
          prompt_injection: "off",
          output_screen: "off",
          output_judge: "block",
          output_judge_on_error: "closed",
          action_screen: "approval",
          action_screen_on_error: "closed",
          output_dlp: "redact",
        },
      },
    };
    expect(readPromptInjection(m)).toBe("off");
    expect(readOutputScreen(m)).toBe("off");
    expect(readOutputJudge(m)).toBe("block");
    expect(readOutputJudgeOnError(m)).toBe("closed");
    expect(readActionScreen(m)).toBe("approval");
    expect(readActionScreenOnError(m)).toBe("closed");
    expect(readOutputDlp(m)).toBe("redact");
    expect(readExtends(m)).toBe("secure-template");
  });
});

describe("form_model defenses setters (default-omission + orphan cleanup)", () => {
  const BASE: AgentManifest = { spec: { model: { provider: "openai" } } };

  it("writing a non-default value adds the defenses key", () => {
    const out = setOutputScreen(BASE, "off");
    expect(out.spec?.defenses?.output_screen).toBe("off");
    // sibling spec fields untouched
    expect(out.spec?.model?.provider).toBe("openai");
  });

  it("writing the default value omits the key and drops an empty defenses block", () => {
    const withOff = setOutputScreen(BASE, "off");
    const backToDefault = setOutputScreen(withOff, "block");
    expect(backToDefault.spec?.defenses).toBeUndefined();
  });

  it("turning the judge off clears the output_judge_on_error orphan", () => {
    const on = setOutputJudge(BASE, "block");
    const withErr = setOutputJudgeOnError(on, "closed");
    expect(withErr.spec?.defenses?.output_judge).toBe("block");
    expect(withErr.spec?.defenses?.output_judge_on_error).toBe("closed");
    const off = setOutputJudge(withErr, "off");
    expect(off.spec?.defenses?.output_judge).toBeUndefined();
    expect(off.spec?.defenses?.output_judge_on_error).toBeUndefined();
  });

  it("turning action_screen off clears the action_screen_on_error orphan", () => {
    const on = setActionScreen(BASE, "block");
    const withErr = setActionScreenOnError(on, "closed");
    const off = setActionScreen(withErr, "off");
    expect(off.spec?.defenses?.action_screen).toBeUndefined();
    expect(off.spec?.defenses?.action_screen_on_error).toBeUndefined();
  });

  it("on_error at its default (open) is omitted", () => {
    const on = setOutputJudge(BASE, "block");
    const openErr = setOutputJudgeOnError(on, "open");
    expect(openErr.spec?.defenses?.output_judge_on_error).toBeUndefined();
    // judge itself stays written
    expect(openErr.spec?.defenses?.output_judge).toBe("block");
  });

  it("setting one switch preserves other defense siblings", () => {
    const a = setOutputScreen(BASE, "off");
    const b = setOutputDlp(a, "redact");
    expect(b.spec?.defenses?.output_screen).toBe("off");
    expect(b.spec?.defenses?.output_dlp).toBe("redact");
  });

  it("does not mutate the input manifest", () => {
    const frozen: AgentManifest = {
      spec: { defenses: { output_dlp: "redact" } },
    };
    const snapshot = JSON.stringify(frozen);
    setOutputScreen(frozen, "off");
    expect(JSON.stringify(frozen)).toBe(snapshot);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor/__tests__/form_model.test.ts`
Expected: FAIL —— import 报错 `readPromptInjection is not exported` / 各符号未定义。

- [ ] **Step 3: 加类型字段**

在 `form_model.ts` 的 `AgentManifest.spec` 对象类型内(现 L116 `subagents?: SubAgentFields[];` 之后、`[k: string]: unknown;` 之前)插入:

```ts
    // Safety posture — the DefenseSpec switches, surfaced in the "defenses" form
    // section. Every field optional; an absent field takes its DefenseSpec
    // default (output_screen=block, prompt_injection=spotlight, rest off/open).
    defenses?: {
      prompt_injection?: string;
      output_screen?: string;
      output_judge?: string;
      output_judge_on_error?: string;
      action_screen?: string;
      action_screen_on_error?: string;
      output_dlp?: string;
      [k: string]: unknown;
    } | null;
    // Template this agent extends (AgentSpecBody.extends). Presence drives the
    // "template may enforce stricter defenses" hint in the defenses section.
    extends?: string;
```

- [ ] **Step 4: 加 `patchDefenses` 助手**

在 `form_model.ts` 的 `patchSpec`(现 L148-151)之后插入:

```ts
// Merge a partial patch into ``spec.defenses`` preserving siblings. A patch
// value of ``undefined`` DELETES that key (a setter signalling "back to the
// DefenseSpec default → omit"). When the merge empties ``defenses``, the whole
// block is dropped so the manifest stays clean (js-yaml omits ``undefined``).
function patchDefenses(
  m: unknown,
  patch: Record<string, string | undefined>,
): AgentManifest {
  const merged: Record<string, unknown> = { ...(specOf(m).defenses ?? {}) };
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) delete merged[k];
    else merged[k] = v;
  }
  return patchSpec(m, {
    defenses: Object.keys(merged).length > 0 ? merged : undefined,
  });
}
```

- [ ] **Step 5: 加读写投影**

追加到 `form_model.ts` 文件尾:

```ts
// ---- defenses (DefenseSpec switches — the "defenses" form section) ----
// Readers return the EFFECTIVE value: an absent key reads as its DefenseSpec
// default so the control shows what the backend would actually apply. Setters
// omit a key whose value equals the default (keeping the YAML minimal) and clear
// the ``_on_error`` sub-knob when its parent switch is turned off.
export const readPromptInjection = (m: unknown): "spotlight" | "off" =>
  (specOf(m).defenses?.prompt_injection as "spotlight" | "off") ?? "spotlight";
export const readOutputScreen = (m: unknown): "block" | "off" =>
  (specOf(m).defenses?.output_screen as "block" | "off") ?? "block";
export const readOutputJudge = (m: unknown): "block" | "off" =>
  (specOf(m).defenses?.output_judge as "block" | "off") ?? "off";
export const readOutputJudgeOnError = (m: unknown): "open" | "closed" =>
  (specOf(m).defenses?.output_judge_on_error as "open" | "closed") ?? "open";
export const readActionScreen = (m: unknown): "off" | "block" | "approval" =>
  (specOf(m).defenses?.action_screen as "off" | "block" | "approval") ?? "off";
export const readActionScreenOnError = (m: unknown): "open" | "closed" =>
  (specOf(m).defenses?.action_screen_on_error as "open" | "closed") ?? "open";
export const readOutputDlp = (m: unknown): "redact" | "off" =>
  (specOf(m).defenses?.output_dlp as "redact" | "off") ?? "off";
export const readExtends = (m: unknown): string | undefined => specOf(m).extends;

export const setPromptInjection = (
  m: unknown,
  v: "spotlight" | "off",
): AgentManifest =>
  patchDefenses(m, { prompt_injection: v === "spotlight" ? undefined : v });

export const setOutputScreen = (m: unknown, v: "block" | "off"): AgentManifest =>
  patchDefenses(m, { output_screen: v === "block" ? undefined : v });

export function setOutputJudge(m: unknown, v: "block" | "off"): AgentManifest {
  if (v === "off") {
    return patchDefenses(m, {
      output_judge: undefined,
      output_judge_on_error: undefined,
    });
  }
  return patchDefenses(m, { output_judge: v });
}

export const setOutputJudgeOnError = (
  m: unknown,
  v: "open" | "closed",
): AgentManifest =>
  patchDefenses(m, { output_judge_on_error: v === "open" ? undefined : v });

export function setActionScreen(
  m: unknown,
  v: "off" | "block" | "approval",
): AgentManifest {
  if (v === "off") {
    return patchDefenses(m, {
      action_screen: undefined,
      action_screen_on_error: undefined,
    });
  }
  return patchDefenses(m, { action_screen: v });
}

export const setActionScreenOnError = (
  m: unknown,
  v: "open" | "closed",
): AgentManifest =>
  patchDefenses(m, { action_screen_on_error: v === "open" ? undefined : v });

export const setOutputDlp = (m: unknown, v: "redact" | "off"): AgentManifest =>
  patchDefenses(m, { output_dlp: v === "off" ? undefined : v });
```

- [ ] **Step 6: 跑测试确认通过 + typecheck**

Run: `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor/__tests__/form_model.test.ts`
Expected: PASS(两个新 describe 全绿,现有用例不受影响)。

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 无错误。

- [ ] **Step 7: 提交**

```bash
git add apps/admin-ui/src/components/manifest-editor/form_model.ts \
        apps/admin-ui/src/components/manifest-editor/__tests__/form_model.test.ts
git commit -m "feat(admin-ui): form_model 防御投影(DefenseSpec 读写 + 默认省略)"
```

---

## Task 2: FormView 防御 section + tab + i18n

**Files:**
- Modify: `apps/admin-ui/src/components/manifest-editor/FormView.tsx`(antd import 加 `Alert`;form_model import 加 Task 1 符号;`FormSection` union 加 `"defenses"`;`sections` map 加 `defenses` JSX)
- Modify: `apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx`(`MANIFEST_TABS` 加 `defenses` 条目)
- Modify: `apps/admin-ui/src/i18n/locales/zh-CN.ts` + `apps/admin-ui/src/i18n/locales/en.ts`(新键)
- Test: `apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx`

**Interfaces:**
- Consumes(Task 1):`readPromptInjection`/`setPromptInjection`/`readOutputScreen`/`setOutputScreen`/`readOutputJudge`/`setOutputJudge`/`readOutputJudgeOnError`/`setOutputJudgeOnError`/`readActionScreen`/`setActionScreen`/`readActionScreenOnError`/`setActionScreenOnError`/`readOutputDlp`/`setOutputDlp`/`readExtends`。
- Produces:`FormSection` union 含 `"defenses"`;`<FormView section="defenses">` 渲染 `data-testid="af-defenses"` section;`MANIFEST_TABS` 含 `defenses` tab。

- [ ] **Step 1: 写失败测试**

追加到 `apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx` 的 `describe("FormView", …)` 块内(复用文件已有的 `renderSection`/`SEED`/`within`/`userEvent`):

```ts
  it("renders the defenses section with every switch/select", () => {
    renderSection("defenses");
    expect(screen.getByTestId("af-defenses")).toBeInTheDocument();
    expect(
      screen.getByTestId("af-defenses-prompt-injection"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("af-defenses-output-screen")).toBeInTheDocument();
    expect(screen.getByTestId("af-defenses-output-judge")).toBeInTheDocument();
    expect(screen.getByTestId("af-defenses-output-dlp")).toBeInTheDocument();
    expect(screen.getByTestId("af-defenses-action-screen")).toBeInTheDocument();
  });

  it("output_screen is on by default; toggling it off writes defenses.output_screen", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("defenses", SEED, onChange);
    const sw = within(
      screen.getByTestId("af-defenses-output-screen"),
    ).getByRole("switch");
    expect(sw).toBeChecked();
    await user.click(sw);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.defenses?.output_screen).toBe("off");
  });

  it("enabling the judge writes defenses.output_judge=block", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("defenses", SEED, onChange);
    const sw = within(
      screen.getByTestId("af-defenses-output-judge"),
    ).getByRole("switch");
    await user.click(sw);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.defenses?.output_judge).toBe("block");
  });

  it("hides the judge on-error select until the judge is enabled", () => {
    renderSection("defenses"); // SEED: judge off
    expect(
      screen.queryByTestId("af-defenses-output-judge-on-error"),
    ).not.toBeInTheDocument();
  });

  it("shows the judge on-error select when the judge is enabled", () => {
    const judged: AgentManifest = {
      ...SEED,
      spec: { ...SEED.spec, defenses: { output_judge: "block" } },
    };
    renderSection("defenses", judged);
    expect(
      screen.getByTestId("af-defenses-output-judge-on-error"),
    ).toBeInTheDocument();
  });

  it("enabling DLP writes defenses.output_dlp=redact", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("defenses", SEED, onChange);
    const sw = within(
      screen.getByTestId("af-defenses-output-dlp"),
    ).getByRole("switch");
    await user.click(sw);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.defenses?.output_dlp).toBe("redact");
  });

  it("turning prompt_injection off writes defenses.prompt_injection=off", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection("defenses", SEED, onChange);
    const sw = within(
      screen.getByTestId("af-defenses-prompt-injection"),
    ).getByRole("switch");
    expect(sw).toBeChecked(); // spotlight default = on
    await user.click(sw);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.defenses?.prompt_injection).toBe("off");
  });

  it("shows the action_screen on-error select only when action_screen != off", () => {
    renderSection("defenses"); // SEED: action_screen off
    expect(
      screen.queryByTestId("af-defenses-action-screen-on-error"),
    ).not.toBeInTheDocument();
    const withAction: AgentManifest = {
      ...SEED,
      spec: { ...SEED.spec, defenses: { action_screen: "block" } },
    };
    renderSection("defenses", withAction);
    expect(
      screen.getByTestId("af-defenses-action-screen-on-error"),
    ).toBeInTheDocument();
  });

  it("shows the extends note only when spec.extends is set", () => {
    renderSection("defenses");
    expect(
      screen.queryByTestId("af-defenses-extends-note"),
    ).not.toBeInTheDocument();
    const withExtends: AgentManifest = {
      ...SEED,
      spec: { ...SEED.spec, extends: "secure-template" },
    };
    renderSection("defenses", withExtends);
    expect(screen.getByTestId("af-defenses-extends-note")).toBeInTheDocument();
  });
```

同时,在文件已有的 `"renders each section's testids under its tab"` 用例尾部补一句(可选但推荐,验证 tab 路由):

```ts
    renderSection("defenses");
    expect(screen.getByTestId("af-defenses")).toBeInTheDocument();
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor/__tests__/FormView.test.tsx`
Expected: FAIL —— `section="defenses"` 未被 `FormSection` 接受(TS 编译错)/ `getByTestId("af-defenses")` 找不到。

- [ ] **Step 3: FormView —— import + FormSection union**

`FormView.tsx` antd import 块(现 L12-21)加 `Alert`:

```ts
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Input,
  InputNumber,
  Select,
  Switch,
  Typography,
} from "antd";
```

`form_model` import 块(现 L32-82)并入 Task 1 的读写符号:

```ts
  readActionScreen,
  readActionScreenOnError,
  readExtends,
  readOutputDlp,
  readOutputJudge,
  readOutputJudgeOnError,
  readOutputScreen,
  readPromptInjection,
  setActionScreen,
  setActionScreenOnError,
  setOutputDlp,
  setOutputJudge,
  setOutputJudgeOnError,
  setOutputScreen,
  setPromptInjection,
```

`FormSection` 类型(现 L90-100)加 `"defenses"`:

```ts
export type FormSection =
  | "basic"
  | "model"
  | "prompt"
  | "tools"
  | "mcp"
  | "knowledge"
  | "skills"
  | "subagents"
  | "memory"
  | "governance"
  | "defenses";
```

- [ ] **Step 4: FormView —— defenses section JSX**

在 `sections` map(`Record<FormSection, ReactNode>`)内加 `defenses` 键(放在 `governance` 键之后)。`Record<FormSection, …>` 会强制该键存在,故加了 union 就必须加此块:

```tsx
    defenses: (
      <section data-testid="af-defenses" style={SECTION}>
        <Heading>
          {t("agent_form.section_defenses")}
          <FieldHelp
            text={t("agent_form.section_defenses_help")}
            testId="af-defenses"
          />
        </Heading>

        {readExtends(formData) !== undefined && (
          <div data-testid="af-defenses-extends-note" style={FIELD}>
            <Alert
              type="info"
              showIcon
              message={t("agent_form.defenses_extends_note")}
            />
          </div>
        )}

        {/* 输入防护 */}
        <Text type="secondary" style={{ display: "block", margin: "0 0 8px" }}>
          {t("agent_form.defenses_group_input")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-prompt-injection">
          <label style={LABEL}>
            {t("agent_form.defenses_prompt_injection")}
            <FieldHelp
              text={t("agent_form.defenses_prompt_injection_help")}
              testId="af-defenses-prompt-injection"
            />
          </label>
          <Switch
            checked={readPromptInjection(formData) === "spotlight"}
            onChange={(on) =>
              onChange(setPromptInjection(formData, on ? "spotlight" : "off"))
            }
          />
          {readPromptInjection(formData) === "off" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_prompt_injection_off_warn")}
            />
          )}
        </div>

        {/* 输出防护 */}
        <Text type="secondary" style={{ display: "block", margin: "16px 0 8px" }}>
          {t("agent_form.defenses_group_output")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-output-screen">
          <label style={LABEL}>
            {t("agent_form.defenses_output_screen")}
            <FieldHelp
              text={t("agent_form.defenses_output_screen_help")}
              testId="af-defenses-output-screen"
            />
          </label>
          <Switch
            checked={readOutputScreen(formData) === "block"}
            onChange={(on) =>
              onChange(setOutputScreen(formData, on ? "block" : "off"))
            }
          />
          {readOutputScreen(formData) === "off" && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_output_screen_off_warn")}
            />
          )}
        </div>

        <div style={FIELD} data-testid="af-defenses-output-judge">
          <label style={LABEL}>
            {t("agent_form.defenses_output_judge")}
            <FieldHelp
              text={t("agent_form.defenses_output_judge_help")}
              testId="af-defenses-output-judge"
            />
          </label>
          <Switch
            checked={readOutputJudge(formData) === "block"}
            onChange={(on) =>
              onChange(setOutputJudge(formData, on ? "block" : "off"))
            }
          />
          {readOutputJudge(formData) === "block" && (
            <div style={{ marginTop: 8 }}>
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 8 }}
                message={t("agent_form.defenses_output_judge_on_warn")}
              />
              <label style={LABEL}>
                {t("agent_form.defenses_output_judge_on_error")}
              </label>
              <Select
                data-testid="af-defenses-output-judge-on-error"
                style={{ width: 240 }}
                value={readOutputJudgeOnError(formData)}
                onChange={(v) =>
                  onChange(
                    setOutputJudgeOnError(
                      formData,
                      v as "open" | "closed",
                    ),
                  )
                }
                options={[
                  { value: "open", label: t("agent_form.defenses_on_error_open") },
                  { value: "closed", label: t("agent_form.defenses_on_error_closed") },
                ]}
              />
            </div>
          )}
        </div>

        <div style={FIELD} data-testid="af-defenses-output-dlp">
          <label style={LABEL}>
            {t("agent_form.defenses_output_dlp")}
            <FieldHelp
              text={t("agent_form.defenses_output_dlp_help")}
              testId="af-defenses-output-dlp"
            />
          </label>
          <Switch
            checked={readOutputDlp(formData) === "redact"}
            onChange={(on) =>
              onChange(setOutputDlp(formData, on ? "redact" : "off"))
            }
          />
          {readOutputDlp(formData) === "redact" && (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 8 }}
              message={t("agent_form.defenses_output_dlp_on_note")}
            />
          )}
        </div>

        {/* 工具行为防护 */}
        <Text type="secondary" style={{ display: "block", margin: "16px 0 8px" }}>
          {t("agent_form.defenses_group_action")}
        </Text>
        <div style={FIELD} data-testid="af-defenses-action-screen">
          <label style={LABEL}>
            {t("agent_form.defenses_action_screen")}
            <FieldHelp
              text={t("agent_form.defenses_action_screen_help")}
              testId="af-defenses-action-screen"
            />
          </label>
          <Select
            data-testid="af-defenses-action-screen-select"
            style={{ width: 240 }}
            value={readActionScreen(formData)}
            onChange={(v) =>
              onChange(
                setActionScreen(
                  formData,
                  v as "off" | "block" | "approval",
                ),
              )
            }
            options={[
              { value: "off", label: t("agent_form.defenses_action_screen_off") },
              { value: "block", label: t("agent_form.defenses_action_screen_block") },
              { value: "approval", label: t("agent_form.defenses_action_screen_approval") },
            ]}
          />
          {readActionScreen(formData) !== "off" && (
            <div style={{ marginTop: 8 }}>
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 8 }}
                message={t("agent_form.defenses_action_screen_on_note")}
              />
              <label style={LABEL}>
                {t("agent_form.defenses_action_screen_on_error")}
              </label>
              <Select
                data-testid="af-defenses-action-screen-on-error"
                style={{ width: 240 }}
                value={readActionScreenOnError(formData)}
                onChange={(v) =>
                  onChange(
                    setActionScreenOnError(
                      formData,
                      v as "open" | "closed",
                    ),
                  )
                }
                options={[
                  { value: "open", label: t("agent_form.defenses_on_error_open") },
                  { value: "closed", label: t("agent_form.defenses_on_error_closed") },
                ]}
              />
            </div>
          )}
        </div>
      </section>
    ),
```

- [ ] **Step 5: ManifestEditor —— 加 tab**

`ManifestEditor.tsx` 的 `MANIFEST_TABS`(现 L25-40),在 `governance` 与 `yaml` 之间插入:

```ts
  { value: "governance", labelKey: "manifest_editor.tab_governance" },
  { value: "defenses", labelKey: "manifest_editor.tab_defenses" },
  { value: "yaml", labelKey: "manifest_editor.tab_yaml" },
```

（`FORM_SECTIONS`/`isFormSection` 从 `MANIFEST_TABS` 派生,自动跟随,无需改。）

- [ ] **Step 6: i18n —— zh-CN + en 新键**

在 `apps/admin-ui/src/i18n/locales/zh-CN.ts`:`manifest_editor` 对象内 `tab_governance` 旁加 `tab_defenses: "防御",`;`agent_form` 对象内(靠近现有 `section_*` 键)加下列键:

```ts
    section_defenses: "防御守卫",
    section_defenses_help:
      "配置该 agent 的安全守卫姿态:输入注入防护、输出筛查/脱敏、工具行为审查。",
    defenses_extends_note:
      "此 agent 继承了模板,模板可能强制比这里更严的防御 —— 你在此调弱的开关可能被模板下限覆盖。",
    defenses_group_input: "输入防护",
    defenses_group_output: "输出防护",
    defenses_group_action: "工具行为防护",
    defenses_prompt_injection: "注入 spotlighting",
    defenses_prompt_injection_help:
      "对不可信来源内容(检索结果、工具输出等)加标记,降低 prompt 注入劫持风险。默认开。",
    defenses_prompt_injection_off_warn:
      "关闭后不再标记不可信内容,降低注入防护。",
    defenses_output_screen: "输出规则筛查",
    defenses_output_screen_help:
      "用规则拦截疑似凭据/外泄形的回复。默认开,建议保持。",
    defenses_output_screen_off_warn:
      "关闭后不再拦截凭据/外泄形回复(默认开,不建议关)。",
    defenses_output_judge: "模型型输出 judge",
    defenses_output_judge_help:
      "用一个模型逐条判定回复是否对齐/泄漏,是规则筛查之上的兜底。",
    defenses_output_judge_on_warn:
      "每条回复额外一次 LLM 调用(增加延迟与成本);并禁用该 agent 的逐-token 流式响应(回复整条一次性返回)。judge 使用的模型在「平台设置」中配置。",
    defenses_output_judge_on_error: "judge 失败时",
    defenses_output_dlp: "输出 PII 脱敏",
    defenses_output_dlp_help:
      "把回复中的 PII(邮箱/手机/身份证/银行卡)替换为 [redacted]。",
    defenses_output_dlp_on_note:
      "会改写含 PII 的合法回复,例如「你的邮箱是 a@b.com」→「你的邮箱是[redacted]」。",
    defenses_action_screen: "工具调用审查",
    defenses_action_screen_help:
      "在每个工具调用执行前判定其是否对齐:关闭 / 拦截 / 转人工审批。",
    defenses_action_screen_off: "关闭",
    defenses_action_screen_block: "拦截",
    defenses_action_screen_approval: "转人工审批",
    defenses_action_screen_on_note:
      "每个工具调用前额外一次判定,增加工具轮延迟(审批模式还会暂停等待人工)。",
    defenses_action_screen_on_error: "审查失败时",
    defenses_on_error_open: "放行(fail-open)",
    defenses_on_error_closed: "拦截(fail-closed)",
```

在 `apps/admin-ui/src/i18n/locales/en.ts` 对应位置加 `tab_defenses: "Defenses",` 与:

```ts
    section_defenses: "Defenses",
    section_defenses_help:
      "Configure this agent's safety posture: input injection defense, output screening/redaction, and tool-action review.",
    defenses_extends_note:
      "This agent extends a template. The template may enforce stricter defenses than shown here — switches you weaken here can be overridden by the template's floor.",
    defenses_group_input: "Input defense",
    defenses_group_output: "Output defense",
    defenses_group_action: "Tool-action defense",
    defenses_prompt_injection: "Injection spotlighting",
    defenses_prompt_injection_help:
      "Marks content from untrusted sources (retrieved data, tool output) to reduce prompt-injection hijack risk. On by default.",
    defenses_prompt_injection_off_warn:
      "Off: untrusted content is no longer marked, weakening injection defense.",
    defenses_output_screen: "Output rule screen",
    defenses_output_screen_help:
      "Rule-based blocking of replies that look like credential leaks / exfiltration. On by default; recommended.",
    defenses_output_screen_off_warn:
      "Off: credential-leak / exfil-shaped replies are no longer blocked (on by default; not recommended).",
    defenses_output_judge: "Model-backed output judge",
    defenses_output_judge_help:
      "Uses a model to judge each reply for alignment / leakage — a backstop above the rule screen.",
    defenses_output_judge_on_warn:
      "Adds one extra LLM call per reply (higher latency and cost); and disables token-by-token streaming for this agent (the reply is returned all at once). The judge model is configured in Platform Settings.",
    defenses_output_judge_on_error: "When the judge fails",
    defenses_output_dlp: "Output PII redaction",
    defenses_output_dlp_help:
      "Replaces PII (email / phone / national ID / card number) in replies with [redacted].",
    defenses_output_dlp_on_note:
      "Rewrites legitimate replies containing PII, e.g. \"your email is a@b.com\" → \"your email is [redacted]\".",
    defenses_action_screen: "Tool-call review",
    defenses_action_screen_help:
      "Judges each tool call for alignment before it runs: off / block / route to human approval.",
    defenses_action_screen_off: "Off",
    defenses_action_screen_block: "Block",
    defenses_action_screen_approval: "Human approval",
    defenses_action_screen_on_note:
      "Adds one judgement before each tool call, increasing latency on tool turns (approval mode also pauses for a human).",
    defenses_action_screen_on_error: "When review fails",
    defenses_on_error_open: "Allow (fail-open)",
    defenses_on_error_closed: "Block (fail-closed)",
```

> 注:i18n 文件结构以现有 `manifest_editor` / `agent_form` 命名空间为准;若两个 locale 文件的对象嵌套层级不同,按各自现有 `section_reflection_evaluator` 键所在层级放置。en.ts 的 apostrophe 字符串按现有文件引号风格转义。

- [ ] **Step 7: 跑测试确认通过 + typecheck**

Run: `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor/__tests__/FormView.test.tsx`
Expected: PASS(新增 defenses 用例全绿;现有用例不受影响)。

Run: `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor/__tests__/ManifestEditor.test.tsx`
Expected: PASS(加 tab 不破现有断言 —— 已核实无 tab-list 精确断言)。

Run: `cd apps/admin-ui && pnpm typecheck`
Expected: 无错误(`Record<FormSection, ReactNode>` 强制 `defenses` 键存在;Select onChange 的 `as` 断言使联合类型对齐)。

- [ ] **Step 8: 提交**

```bash
git add apps/admin-ui/src/components/manifest-editor/FormView.tsx \
        apps/admin-ui/src/components/manifest-editor/ManifestEditor.tsx \
        apps/admin-ui/src/i18n/locales/zh-CN.ts \
        apps/admin-ui/src/i18n/locales/en.ts \
        apps/admin-ui/src/components/manifest-editor/__tests__/FormView.test.tsx
git commit -m "feat(admin-ui): Agent 表单防御守卫 section + tab(7 开关 + 影响告警)"
```

---

## 最终验证(全部任务后)

- [ ] `cd apps/admin-ui && pnpm exec vitest run src/components/manifest-editor` —— 整个 manifest-editor 测试目录全绿。
- [ ] `cd apps/admin-ui && pnpm typecheck` —— 零类型错误。
- [ ] 手动冒烟(可选):`pnpm --filter admin-ui dev`,进 Agent 创建/编辑 → "防御" tab → 拨各开关 → 切 YAML tab,确认 `defenses:` 块按默认省略规则正确生成;judge 开启显示告警 + on-error;含 `extends:` 的 manifest 顶部显示提示。

---

## Self-Review(计划作者已跑)

**1. Spec coverage:**
- spec §2 全 7 开关 → Task 1 读写 + Task 2 控件 ✅
- spec §3.1 默认省略 + on_error 孤儿清理 → Task 1 Step 4/5 + 测试 ✅
- spec §3.2 分组 section → Task 2 Step 4(输入/输出/工具行为三组)✅
- spec §3.3 五条影响告警 → Task 2 Step 4(judge/screen-off/dlp/action/prompt-injection-off)✅
- spec §3.4 tab + i18n → Task 2 Step 5/6 ✅
- spec §5 决策 2:judge 平台级、仅开关、告警指向平台设置 → judge 告警文案含"在平台设置中配置" ✅
- spec §5 决策 2a:extends 轻量提示 → Task 2 extends note + 测试 ✅
- spec §7 排期措辞:judge 告警含"禁用流式" → 文案已含 ✅
- spec §8 测试:回显/拨动/联动显隐/extends/默认省略 → Task 1+2 测试覆盖 ✅

**2. Placeholder scan:** 无 TBD/TODO;每个 code step 都是完整可粘贴代码;命令含预期输出。✅

**3. Type consistency:** `read*/set*` 签名 Task 1 定义、Task 2 import 一致;`FormSection` union 加 `"defenses"` 与 `sections` map 键匹配;Select `as` 断言对齐 setter 联合类型。✅

> spec §8 提到的 "YAML round-trip" 测试(手写 defenses YAML → 切 Form → 回填)属 ManifestEditor 层集成,本计划靠 Task 1 读投影 + Task 2 渲染分别覆盖(read* 从 spec.defenses 回填控件已由 FormView 测试的 seeded-state 用例验证:judge/action seeded=block → 控件与 on-error 正确显示)。若 reviewer 认为需显式端到端 round-trip 测试,补一条 ManifestEditor.test.tsx 用例即可,非阻塞。
