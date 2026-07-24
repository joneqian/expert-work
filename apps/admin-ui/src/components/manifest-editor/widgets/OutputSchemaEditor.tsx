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
 * Field-name validation (``NAME_RE``) is enforced locally: rows are held in
 * component state (not read directly off ``formData`` on every keystroke) so
 * an in-progress, momentarily-invalid name can still be typed and shown with
 * an error state WITHOUT ever reaching ``onChange`` — the manifest never
 * gains a property whose key fails the pattern. A commit (write to
 * ``formData``) only happens once every row's name is valid; other edits
 * (type/required/description, add, remove) go through the same gate so a
 * blank in-progress row never leaks into the manifest either.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
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

  const [rows, setRowsState] = useState<SchemaFieldRow[]>(externalRows);
  // Resync from the manifest whenever it changes from the outside (incl. our
  // own successful commits, a harmless no-op since local state already
  // equals what we just wrote). A BLOCKED edit (invalid name — see the
  // module doc comment) never calls ``onChange``, so ``formData`` doesn't
  // change and this effect doesn't clobber the in-progress local edit.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (Array.isArray(external)) setRowsState(external);
  }, [formData]);

  const typeOptions = FIELD_TYPES.map((type) => ({
    value: type,
    label: t(`agent_form.output_schema.type_${type}`),
  }));

  const commit = (next: SchemaFieldRow[]): void => {
    setRowsState(next);
    if (next.every((r) => NAME_RE.test(r.name))) {
      onChange(setOutputSchemaRows(formData, next));
    }
  };
  const patchRow = (i: number, patch: Partial<SchemaFieldRow>): void =>
    commit(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = (): void =>
    commit([...rows, { name: "", type: "string", required: false, description: "" }]);
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
                const nameInvalid = !NAME_RE.test(row.name);
                return (
                  <div
                    key={i}
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
                          {t("agent_form.output_schema.name_invalid")}
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
