/**
 * OutputSchemaEditor — config-page redesign v2 Task 7. A flat field-list
 * visualization over ``spec.output_schema.json_schema`` (Stream RT-1 /
 * RT-ADR-4's structured-final-reply JSON Schema): each row is one top-level
 * property (name/type/required/description). Replaces the former FormView
 * block that only surfaced on/off state with a "go edit the YAML" hint.
 *
 * Three render states (``readOutputSchemaRows``):
 *  - not configured (``undefined``) — Switch off + one hint line.
 *  - flat (``SchemaFieldRow[]``) — Switch on + the row table + "add field".
 *  - ``"unrepresentable"`` (configured but not flat, e.g. hand-authored
 *    nested/``$ref``/``oneOf`` in the YAML view) — the Switch is HIDDEN
 *    entirely and a read-only info Alert points at the YAML view. No control
 *    in this state ever calls ``onChange`` — editing a structure this widget
 *    can't parse would silently mangle it.
 *
 * ``name``/``strict`` never surface here — ``setOutputSchemaRows`` preserves
 * them untouched (see form_model.ts's own doc comment).
 *
 * Field-name validation is enforced locally, PER ROW — there is no
 * whole-table gate. Rows are held in component state as ``LocalRow[]``
 * (a ``SchemaFieldRow`` plus a locally-generated ``uid`` used as the React
 * key, never the field name — a row's name is exactly what's mid-edit and
 * transiently non-unique/invalid). A row is locally invalid (``rowIssue``)
 * when its name fails ``NAME_RE`` (blank, mid-edit, leading digit, …) OR
 * duplicates an EARLIER row's name — the duplicate case matters because
 * ``properties`` is a record: two same-named rows in one write would
 * last-wins overwrite the earlier property (destroying its type/description)
 * the moment a new name transits through an existing one ("title" while
 * typing "title2"). Every edit (patch/add/remove) commits immediately: the
 * write to ``formData`` is ``setOutputSchemaRows`` of whichever rows are
 * CURRENTLY locally valid, in their local order. An invalid row is simply
 * excluded from that write — it stays visible locally with an error state
 * (a duplicate-specific message for the collision case), but every OTHER
 * row's edit still lands in the manifest on its own, so one bad/blank/
 * colliding row never blocks unrelated edits (own or sibling-field) from
 * being saved. Fixing the name re-includes the row on the next keystroke.
 *
 * Resync from outside: an effect keyed on the ``formData`` prop re-derives
 * rows via ``readOutputSchemaRows`` whenever it changes. Nothing else in the
 * app writes ``spec.output_schema``, so any change is one of two shapes: (a)
 * an echo of our OWN last commit (or a sibling control's edit that leaves
 * ``output_schema`` untouched) — detected by comparing against
 * ``lastWrittenRef`` and skipped entirely, so it can never clobber an
 * in-progress WIP row; or (b) a genuine external change (e.g. the initial
 * mount's seed) — synced in, with any local WIP (locally-invalid) rows
 * preserved and appended after the synced ones rather than dropped.
 * ``lastWrittenRef`` holds the READBACK (``readOutputSchemaRows``) of the
 * manifest each commit produced — not the row array we passed in — so even
 * if a future write path ever produced a manifest whose readback differs
 * from its input rows (e.g. a record-collapse), the echo would still be
 * recognized as our own instead of tripping the external branch. A WIP row
 * IS lost across a full unmount (e.g. switching config groups, or toggling
 * the YAML view — both swap this widget out of the tree rather than just
 * changing its props) — accepted; nothing survives an unmount for any
 * widget in this editor.
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { App, Alert, Button, Checkbox, Input, Select, Switch, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  readOutputSchemaRows,
  setOutputSchemaRows,
  type SchemaFieldRow,
  type SchemaFieldType,
} from "../form_model";

const { Text } = Typography;

const SECTION: CSSProperties = { marginBottom: 24 };
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const FIELD_TYPES: SchemaFieldType[] = [
  "string",
  "number",
  "integer",
  "boolean",
  "array_string",
  "array_number",
];

// A row plus a locally-generated identity, stable across re-renders and
// independent of ``name`` (which is exactly the field that's transiently
// invalid/non-unique while being typed).
interface LocalRow extends SchemaFieldRow {
  uid: string;
}

function stripUid(row: LocalRow): SchemaFieldRow {
  const { name, type, required, description } = row;
  return { name, type, required, description };
}

// Per-row local validity — see the module doc comment. ``"invalid"`` = the
// name fails NAME_RE; ``"duplicate"`` = the name matches an EARLIER row's
// (first occurrence wins — only the later row is flagged, so an existing
// row never turns invalid because someone below is typing through its name).
type RowIssue = "invalid" | "duplicate" | null;

function rowIssue(rows: readonly { name: string }[], i: number): RowIssue {
  if (!NAME_RE.test(rows[i].name)) return "invalid";
  for (let j = 0; j < i; j++) {
    if (rows[j].name === rows[i].name) return "duplicate";
  }
  return null;
}

// Positional content equality (ignoring ``uid``) — used to tell an echo of
// our own commit (or a sibling-field edit that leaves this block untouched)
// apart from a genuine external change to ``output_schema`` itself.
function sameRows(a: SchemaFieldRow[], b: SchemaFieldRow[]): boolean {
  return (
    a.length === b.length &&
    a.every(
      (r, i) =>
        r.name === b[i].name &&
        r.type === b[i].type &&
        r.required === b[i].required &&
        r.description === b[i].description,
    )
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 style={{ fontSize: 15, margin: "0 0 12px" }}>{children}</h3>;
}

interface OutputSchemaEditorProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

export function OutputSchemaEditor({ formData, onChange }: OutputSchemaEditorProps) {
  const { t } = useTranslation();
  const { modal } = App.useApp();

  const external = readOutputSchemaRows(formData);
  const unrepresentable = external === "unrepresentable";
  const on = external !== undefined;
  const externalRows = Array.isArray(external) ? external : [];

  const nextUidRef = useRef(0);
  const makeUid = (): string => `row-${nextUidRef.current++}`;

  const [rows, setRowsState] = useState<LocalRow[]>(() =>
    externalRows.map((r) => ({ ...r, uid: makeUid() })),
  );
  // The readback of the manifest this widget itself last wrote (or, at
  // mount, the externally-seeded rows) — see the module doc comment's
  // "resync from outside" paragraph for what this distinguishes.
  const lastWrittenRef = useRef<SchemaFieldRow[]>(externalRows);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!Array.isArray(external)) return;
    if (sameRows(external, lastWrittenRef.current)) return;
    lastWrittenRef.current = external;
    setRowsState((prev) => [
      ...external.map((r) => ({ ...r, uid: makeUid() })),
      ...prev.filter((_, i) => rowIssue(prev, i) !== null),
    ]);
  }, [formData]);

  const typeOptions = FIELD_TYPES.map((type) => ({
    value: type,
    label: t(`agent_form.output_schema.type_${type}`),
  }));

  // No whole-table gate: always writes, but only the rows that are CURRENTLY
  // locally valid (name pattern + no duplicate-of-earlier) — see the module
  // doc comment. ``lastWrittenRef`` gets the READBACK of the produced
  // manifest, not the input subset (echo hardening, ibid.).
  const commit = (next: LocalRow[]): void => {
    setRowsState(next);
    const validSubset = next
      .filter((_, i) => rowIssue(next, i) === null)
      .map(stripUid);
    const written = setOutputSchemaRows(formData, validSubset);
    const readback = readOutputSchemaRows(written);
    lastWrittenRef.current = Array.isArray(readback) ? readback : validSubset;
    onChange(written);
  };
  const patchRow = (i: number, patch: Partial<SchemaFieldRow>): void =>
    commit(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = (): void =>
    commit([
      ...rows,
      { name: "", type: "string", required: false, description: "", uid: makeUid() },
    ]);
  const removeRow = (i: number): void =>
    commit(rows.filter((_, idx) => idx !== i));

  const handleToggle = (checked: boolean): void => {
    if (checked) {
      commit([]);
      return;
    }
    if (rows.length === 0) {
      onChange(setOutputSchemaRows(formData, null));
      return;
    }
    modal.confirm({
      title: t("agent_form.output_schema.off_confirm"),
      onOk: () => onChange(setOutputSchemaRows(formData, null)),
    });
  };

  return (
    <section data-testid="af-output-schema" style={SECTION}>
      <Heading>{t("agent_form.section_output_schema")}</Heading>
      {unrepresentable ? (
        <Alert
          type="info"
          showIcon
          data-testid="af-output-schema-readonly"
          message={t("agent_form.output_schema.complex_readonly")}
        />
      ) : (
        <>
          <div
            style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}
          >
            <Switch
              checked={on}
              data-testid="af-output-schema-switch"
              aria-label={t("agent_form.output_schema.on_label")}
              onChange={handleToggle}
            />
            <Text>{t("agent_form.output_schema.on_label")}</Text>
          </div>
          {!on && (
            <Text type="secondary">{t("agent_form.output_schema.hint_off")}</Text>
          )}
          {on && (
            <>
              <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                <span style={{ width: 160, fontSize: 12, color: "var(--ew-text-tertiary, #888)" }}>
                  {t("agent_form.output_schema.col_name")}
                </span>
                <span style={{ width: 140, fontSize: 12, color: "var(--ew-text-tertiary, #888)" }}>
                  {t("agent_form.output_schema.col_type")}
                </span>
                <span style={{ width: 60, fontSize: 12, color: "var(--ew-text-tertiary, #888)" }}>
                  {t("agent_form.output_schema.col_required")}
                </span>
                <span style={{ flex: 1, fontSize: 12, color: "var(--ew-text-tertiary, #888)" }}>
                  {t("agent_form.output_schema.col_desc")}
                </span>
              </div>
              {rows.map((row, i) => {
                const issue = rowIssue(rows, i);
                const nameInvalid = issue !== null;
                return (
                  <div
                    key={row.uid}
                    data-testid={`af-output-schema-row-${i}`}
                    style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "flex-start" }}
                  >
                    <div style={{ width: 160 }}>
                      <Input
                        value={row.name}
                        status={nameInvalid ? "error" : undefined}
                        aria-invalid={nameInvalid}
                        data-testid={`af-output-schema-name-${i}`}
                        aria-label={t("agent_form.output_schema.col_name")}
                        onChange={(e) => patchRow(i, { name: e.target.value })}
                      />
                      {nameInvalid && (
                        <Text
                          type="danger"
                          style={{ fontSize: 12, display: "block" }}
                        >
                          {t(
                            issue === "duplicate"
                              ? "agent_form.output_schema.name_duplicate"
                              : "agent_form.output_schema.name_invalid",
                          )}
                        </Text>
                      )}
                    </div>
                    <Select
                      style={{ width: 140 }}
                      value={row.type}
                      options={typeOptions}
                      data-testid={`af-output-schema-type-${i}`}
                      aria-label={t("agent_form.output_schema.col_type")}
                      onChange={(v: SchemaFieldType) => patchRow(i, { type: v })}
                    />
                    <div style={{ width: 60, paddingTop: 6, textAlign: "center" }}>
                      <Checkbox
                        checked={row.required}
                        data-testid={`af-output-schema-required-${i}`}
                        aria-label={t("agent_form.output_schema.col_required")}
                        onChange={(e) => patchRow(i, { required: e.target.checked })}
                      />
                    </div>
                    <Input
                      style={{ flex: 1 }}
                      value={row.description}
                      data-testid={`af-output-schema-desc-${i}`}
                      aria-label={t("agent_form.output_schema.col_desc")}
                      onChange={(e) => patchRow(i, { description: e.target.value })}
                    />
                    <Button
                      type="text"
                      danger
                      size="small"
                      data-testid={`af-output-schema-remove-${i}`}
                      aria-label={t("agent_form.output_schema.remove_field")}
                      onClick={() => removeRow(i)}
                    >
                      {t("agent_form.output_schema.remove_field")}
                    </Button>
                  </div>
                );
              })}
              <Button
                type="dashed"
                size="small"
                data-testid="af-output-schema-add"
                onClick={addRow}
              >
                {t("agent_form.output_schema.add_field")}
              </Button>
            </>
          )}
        </>
      )}
    </section>
  );
}
