/**
 * FieldDef / PolicyFieldList — the declarative config-array pattern that
 * replaces hand-written ``FieldRow`` blocks (the shape ``RunBudgetSection``,
 * Task 6 of PR1, wrote by hand: five near-identical blocks of
 * label/brief/impact/default-badge plumbing around a single antd control).
 * A ``FieldDef`` is a data row describing one manifest field; ``PolicyFieldList``
 * renders one ``FieldRow`` per def, resolving its copy via the
 * ``${i18nKey}_label`` / ``_brief`` / ``_impact`` / ``_default`` convention and
 * wiring its control (``InputNumber`` for number/percent, ``Switch`` for
 * switch, antd ``Select`` for select/tags) to a single ``onPatch`` callback —
 * so a new field group is a data table, not a page of near-duplicate JSX.
 *
 * ``_impact`` and ``_default`` may be omitted per field (no impact note /
 * no default-value badge); presence is checked at render time via
 * ``i18n.exists`` rather than assumed, since not every field needs either.
 *
 * select/tags (PR3 Task 1) widen the value domain beyond number/boolean to
 * string / string-array; ``PolicyFieldList`` is generic over that domain
 * (``V extends FieldValue``) purely so existing number/boolean-only callers
 * (``RunBudgetSection``, ``ContextGatesSection``) keep their narrower
 * ``onPatch`` signatures — TS's contravariant callback-parameter check would
 * otherwise reject a ``(patch: Record<string, number|boolean|undefined>)``
 * handler once the prop type itself included string/array.
 *
 * ``PolicyFieldTable`` (Task 2 of a later redesign PR) is a second renderer
 * over the same ``FieldDef`` data: a dense table layout (配置项|值|默认|说明)
 * for groups where a full FieldRow-per-field stack is too tall, grouped by
 * an optional ``titleKey`` header row. It shares ``FieldControl`` with
 * ``PolicyFieldList`` so both renderers dispatch controls identically.
 */
import { Fragment, type ReactNode } from "react";
import { InputNumber, Select, Switch, Tag } from "antd";
import { useTranslation } from "react-i18next";

import { FieldRow } from "../FieldRow";

export interface FieldDef {
  /** manifest 路径,= FieldRow data-field-id,也是 i18n 键的后缀源 */
  fieldId: string;
  /** i18n 命名空间前缀,如 "run_budget.max_iterations" → label/_brief/_impact/_default 四键 */
  i18nKey: string;
  /** 读写键,对应 read/patch helper 返回对象的字段名 */
  valueKey: string;
  kind: "number" | "switch" | "percent" | "select" | "tags"; // percent = 0–1 浮点,InputNumber step 0.05
  effectiveDefault: number | boolean | string | readonly string[] | null; // 显示层 effective 默认(isDefault 判定 + 徽章)
  min?: number;
  max?: number;
  /** select 专用:候选值列表,值本身即 option value */
  options?: readonly string[];
  /** select 专用:option label 的 i18n 前缀,`${optionLabelKey}_${option}`;缺省则显示裸值 */
  optionLabelKey?: string;
}

type FieldValue = number | boolean | string | readonly string[];

export interface PolicyFieldListProps<V extends FieldValue = FieldValue> {
  defs: readonly FieldDef[];
  values: Record<string, V | undefined>; // read helper 输出
  onPatch: (patch: Record<string, V | undefined>) => void;
  /** i18n 四键约定:`${i18nKey}_label` `_brief` `_impact` `_default`(_impact/_default 可缺省) */
}

function arraysEqual(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function isAtDefault(
  raw: FieldValue | undefined,
  effectiveDefault: FieldDef["effectiveDefault"],
): boolean {
  if (raw === undefined) return true;
  if (Array.isArray(raw) || Array.isArray(effectiveDefault)) {
    return arraysEqual(
      Array.isArray(raw) ? raw : [],
      Array.isArray(effectiveDefault) ? effectiveDefault : [],
    );
  }
  return raw === effectiveDefault;
}

function numericValue(
  raw: FieldValue | undefined,
  effectiveDefault: FieldDef["effectiveDefault"],
): number | null {
  if (typeof raw === "number") return raw;
  return typeof effectiveDefault === "number" ? effectiveDefault : null;
}

function booleanValue(
  raw: FieldValue | undefined,
  effectiveDefault: FieldDef["effectiveDefault"],
): boolean {
  if (typeof raw === "boolean") return raw;
  return typeof effectiveDefault === "boolean" ? effectiveDefault : false;
}

function stringValue(
  raw: FieldValue | undefined,
  effectiveDefault: FieldDef["effectiveDefault"],
): string | undefined {
  if (typeof raw === "string") return raw;
  return typeof effectiveDefault === "string" ? effectiveDefault : undefined;
}

function tagsValue(
  raw: FieldValue | undefined,
  effectiveDefault: FieldDef["effectiveDefault"],
): string[] {
  if (Array.isArray(raw)) return [...raw] as string[];
  return Array.isArray(effectiveDefault) ? ([...effectiveDefault] as string[]) : [];
}

/**
 * FieldControl — the ``def.kind``-dispatched control renderer that used to
 * live inline in ``PolicyFieldList``'s map (switch/select/tags/number, each
 * wired to the same ``onPatch`` convention: patch the explicit value, or an
 * explicit ``undefined`` when the new value round-trips back to
 * ``effectiveDefault``). Pulled out as its own component (Task 2 of a later
 * redesign PR) so ``PolicyFieldTable`` can reuse the exact same control
 * rendering without ``PolicyFieldList``'s ``FieldRow``-per-def layout.
 */
export function FieldControl<V extends FieldValue = FieldValue>({
  def,
  raw,
  label,
  onPatch,
}: {
  def: FieldDef;
  raw: V | undefined;
  label: string;
  onPatch: (patch: Record<string, V | undefined>) => void;
}): ReactNode {
  const { t } = useTranslation();

  return def.kind === "switch" ? (
    <Switch
      checked={booleanValue(raw, def.effectiveDefault)}
      aria-label={label}
      onChange={(checked) =>
        onPatch({
          [def.valueKey]:
            checked === def.effectiveDefault ? undefined : checked,
        } as Record<string, V | undefined>)
      }
    />
  ) : def.kind === "select" ? (
    <Select
      value={stringValue(raw, def.effectiveDefault)}
      aria-label={label}
      style={{ width: "100%" }}
      options={(def.options ?? []).map((opt) => ({
        value: opt,
        label: def.optionLabelKey
          ? t(`${def.optionLabelKey}_${opt}`)
          : opt,
      }))}
      onChange={(v: string) =>
        onPatch({
          [def.valueKey]: v === def.effectiveDefault ? undefined : v,
        } as Record<string, V | undefined>)
      }
    />
  ) : def.kind === "tags" ? (
    <Select
      mode="tags"
      value={tagsValue(raw, def.effectiveDefault)}
      aria-label={label}
      style={{ width: "100%" }}
      onChange={(v: string[]) =>
        onPatch({
          [def.valueKey]: v.length === 0 ? undefined : v,
        } as Record<string, V | undefined>)
      }
    />
  ) : (
    <InputNumber
      min={def.min ?? (def.kind === "percent" ? 0.05 : undefined)}
      max={def.max ?? (def.kind === "percent" ? 1 : undefined)}
      step={def.kind === "percent" ? 0.05 : undefined}
      value={numericValue(raw, def.effectiveDefault)}
      aria-label={label}
      onChange={(v) =>
        onPatch({
          [def.valueKey]: v ?? undefined,
        } as Record<string, V | undefined>)
      }
    />
  );
}

export function PolicyFieldList<V extends FieldValue = FieldValue>({
  defs,
  values,
  onPatch,
}: PolicyFieldListProps<V>): ReactNode {
  const { t, i18n } = useTranslation();

  return (
    <>
      {defs.map((def) => {
        const raw = values[def.valueKey];
        const atDefault = isAtDefault(raw, def.effectiveDefault);
        const defaultKey = `${def.i18nKey}_default`;
        const impactKey = `${def.i18nKey}_impact`;
        const label = t(`${def.i18nKey}_label`);

        const badgeValue =
          def.kind === "switch" || def.kind === "tags"
            ? undefined
            : atDefault
              ? i18n.exists(defaultKey)
                ? t(defaultKey)
                : undefined
              : String(raw);
        const impact = i18n.exists(impactKey) ? t(impactKey) : undefined;

        return (
          <FieldRow
            key={def.fieldId}
            fieldId={def.fieldId}
            label={label}
            brief={t(`${def.i18nKey}_brief`)}
            impact={impact}
            defaultValue={badgeValue}
            isDefault={atDefault}
          >
            <FieldControl def={def} raw={raw} label={label} onPatch={onPatch} />
          </FieldRow>
        );
      })}
    </>
  );
}

export interface FieldGroup {
  titleKey?: string;
  defs: readonly FieldDef[];
}
export interface PolicyFieldTableProps<V extends FieldValue = FieldValue> {
  groups: readonly FieldGroup[];
  values: Record<string, V | undefined>;
  onPatch: (patch: Record<string, V | undefined>) => void;
}

/**
 * PolicyFieldTable — a table-layout renderer over the same ``FieldDef``
 * data ``PolicyFieldList`` consumes: one row per field (配置项|值|默认|说明),
 * grouped into optional titled sections. Unlike ``FieldRow``'s stacked
 * layout, the 说明 column always shows both brief and impact — there is no
 * per-field collapse here either, matching ``FieldRow``'s Task 2 change.
 * The outer ``overflowX: auto`` wrapper keeps a narrow panel from being
 * stretched wide by the table rather than scrolling horizontally.
 */
export function PolicyFieldTable<V extends FieldValue = FieldValue>({
  groups,
  values,
  onPatch,
}: PolicyFieldTableProps<V>): ReactNode {
  const { t, i18n } = useTranslation();
  return (
    <div style={{ overflowX: "auto" }}>
      <table
        data-testid="policy-field-table"
        style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
      >
        <colgroup>
          <col style={{ width: "22%" }} />
          <col style={{ width: "22%" }} />
          <col style={{ width: "10%" }} />
          <col />
        </colgroup>
        <tbody>
          {groups.map((group, gi) => (
            <Fragment key={group.titleKey ?? gi}>
              {group.titleKey && (
                <tr>
                  <td
                    colSpan={4}
                    style={{ fontWeight: 600, padding: "12px 8px 4px", borderTop: gi > 0 ? "1px solid var(--ew-border-subtle)" : undefined }}
                  >
                    {t(group.titleKey)}
                  </td>
                </tr>
              )}
              {group.defs.map((def) => {
                const raw = values[def.valueKey];
                const atDefault = isAtDefault(raw, def.effectiveDefault);
                const label = t(`${def.i18nKey}_label`);
                const brief = t(`${def.i18nKey}_brief`);
                const impactKey = `${def.i18nKey}_impact`;
                const impact = i18n.exists(impactKey) ? t(impactKey) : undefined;
                const defaultKey = `${def.i18nKey}_default`;
                const badge =
                  def.kind === "switch" || def.kind === "tags"
                    ? undefined
                    : atDefault
                      ? (i18n.exists(defaultKey) ? t(defaultKey) : undefined)
                      : String(raw);
                return (
                  <tr key={def.fieldId} data-field-id={def.fieldId} style={{ verticalAlign: "top" }}>
                    <td style={{ padding: "8px", color: "var(--ew-text-primary)" }}>{label}</td>
                    <td style={{ padding: "8px" }}>
                      <FieldControl def={def} raw={raw} label={label} onPatch={onPatch} />
                    </td>
                    <td style={{ padding: "8px" }}>
                      {badge !== undefined && (
                        <Tag color={atDefault ? undefined : "blue"} bordered={false}>
                          {atDefault ? t("manifest_editor.field_default_badge", { value: badge }) : badge}
                        </Tag>
                      )}
                    </td>
                    <td style={{ padding: "8px", color: "var(--ew-text-secondary)", fontSize: 12, lineHeight: 1.5 }}>
                      <div>{brief}</div>
                      {impact && <div style={{ marginTop: 2 }}>{impact}</div>}
                    </td>
                  </tr>
                );
              })}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
