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
 */
import type { ReactNode } from "react";
import { InputNumber, Select, Switch } from "antd";
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

        const control =
          def.kind === "switch" ? (
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
            {control}
          </FieldRow>
        );
      })}
    </>
  );
}
