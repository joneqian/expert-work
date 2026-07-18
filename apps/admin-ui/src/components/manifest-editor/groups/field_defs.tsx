/**
 * FieldDef / PolicyFieldList — the declarative config-array pattern that
 * replaces hand-written ``FieldRow`` blocks (the shape ``RunBudgetSection``,
 * Task 6 of PR1, wrote by hand: five near-identical blocks of
 * label/brief/impact/default-badge plumbing around a single antd control).
 * A ``FieldDef`` is a data row describing one manifest field; ``PolicyFieldList``
 * renders one ``FieldRow`` per def, resolving its copy via the
 * ``${i18nKey}_label`` / ``_brief`` / ``_impact`` / ``_default`` convention and
 * wiring its control (``InputNumber`` for number/percent, ``Switch`` for
 * switch) to a single ``onPatch`` callback — so a new field group is a data
 * table, not a page of near-duplicate JSX.
 *
 * ``_impact`` and ``_default`` may be omitted per field (no impact note /
 * no default-value badge); presence is checked at render time via
 * ``i18n.exists`` rather than assumed, since not every field needs either.
 */
import type { ReactNode } from "react";
import { InputNumber, Switch } from "antd";
import { useTranslation } from "react-i18next";

import { FieldRow } from "../FieldRow";

export interface FieldDef {
  /** manifest 路径,= FieldRow data-field-id,也是 i18n 键的后缀源 */
  fieldId: string;
  /** i18n 命名空间前缀,如 "run_budget.max_iterations" → label/_brief/_impact/_default 四键 */
  i18nKey: string;
  /** 读写键,对应 read/patch helper 返回对象的字段名 */
  valueKey: string;
  kind: "number" | "switch" | "percent"; // percent = 0–1 浮点,InputNumber step 0.05
  effectiveDefault: number | boolean | null; // 显示层 effective 默认(isDefault 判定 + 徽章)
  min?: number;
  max?: number;
}

export interface PolicyFieldListProps {
  defs: readonly FieldDef[];
  values: Record<string, number | boolean | undefined>; // read helper 输出
  onPatch: (patch: Record<string, number | boolean | undefined>) => void;
  /** i18n 四键约定:`${i18nKey}_label` `_brief` `_impact` `_default`(_impact/_default 可缺省) */
}

function isAtDefault(
  raw: number | boolean | undefined,
  effectiveDefault: number | boolean | null,
): boolean {
  return raw === undefined || raw === effectiveDefault;
}

function numericValue(
  raw: number | boolean | undefined,
  effectiveDefault: number | boolean | null,
): number | null {
  if (typeof raw === "number") return raw;
  return typeof effectiveDefault === "number" ? effectiveDefault : null;
}

function booleanValue(
  raw: number | boolean | undefined,
  effectiveDefault: number | boolean | null,
): boolean {
  if (typeof raw === "boolean") return raw;
  return typeof effectiveDefault === "boolean" ? effectiveDefault : false;
}

export function PolicyFieldList({
  defs,
  values,
  onPatch,
}: PolicyFieldListProps): ReactNode {
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
          def.kind === "switch"
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
                })
              }
            />
          ) : (
            <InputNumber
              min={def.min ?? (def.kind === "percent" ? 0.05 : undefined)}
              max={def.max ?? (def.kind === "percent" ? 1 : undefined)}
              step={def.kind === "percent" ? 0.05 : undefined}
              value={numericValue(raw, def.effectiveDefault)}
              aria-label={label}
              onChange={(v) => onPatch({ [def.valueKey]: v ?? undefined })}
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
