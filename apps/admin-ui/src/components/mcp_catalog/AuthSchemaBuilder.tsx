/**
 * Auth-schema field builder — Stream W.
 *
 * Lets a platform admin compose the ``auth_schema.fields`` of a catalog
 * connector: a list of ``{ key, label, kind, required }`` rows the tenant
 * must fill in at instantiation. ``kind="secret"`` fields are stored
 * encrypted; ``kind="param"`` fields are substituted into the URL template.
 *
 * Controlled component — the parent owns the ``value`` array and receives the
 * next array via ``onChange`` (immutable updates, never mutate in place).
 */
import { Button, Checkbox, Input, Select, Space, Typography } from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { McpCatalogAuthField, McpAuthFieldKind } from "../../api/mcp-catalog";

/**
 * A builder row carries an internal-only ``_uid`` used as a stable React key
 * so removing a middle row does not bleed input values between siblings
 * (slugs start empty, so ``key`` is not a reliable React key). ``_uid`` is
 * stripped before the auth_schema payload is sent to the backend — see
 * ``stripAuthFieldUids`` below, which the catalog drawer applies on submit.
 */
export interface AuthSchemaBuilderField extends McpCatalogAuthField {
  _uid?: string;
}

/** Strip the internal-only ``_uid`` so the backend (extra="forbid") accepts
 *  the auth_schema payload. */
export function stripAuthFieldUids(
  fields: AuthSchemaBuilderField[],
): McpCatalogAuthField[] {
  return fields.map(({ _uid: _drop, ...field }) => field);
}

export interface AuthSchemaBuilderProps {
  value: AuthSchemaBuilderField[];
  onChange: (next: AuthSchemaBuilderField[]) => void;
}

const KIND_OPTIONS: { value: McpAuthFieldKind; labelKey: string }[] = [
  { value: "param", labelKey: "mcp_catalog.field_kind_param" },
  { value: "secret", labelKey: "mcp_catalog.field_kind_secret" },
];

export function AuthSchemaBuilder({ value, onChange }: AuthSchemaBuilderProps) {
  const { t } = useTranslation();

  const update = (index: number, patch: Partial<McpCatalogAuthField>) => {
    onChange(value.map((field, i) => (i === index ? { ...field, ...patch } : field)));
  };

  const remove = (index: number) => {
    onChange(value.filter((_, i) => i !== index));
  };

  const add = () => {
    onChange([
      ...value,
      { _uid: crypto.randomUUID(), key: "", label: "", kind: "param", required: true },
    ]);
  };

  return (
    <div data-testid="asb-root">
      {value.length === 0 && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t("mcp_catalog.field_builder_empty")}
        </Typography.Text>
      )}
      <Space direction="vertical" style={{ width: "100%" }} size={8}>
        {value.map((field, index) => (
          <Space.Compact
            key={field._uid ?? `existing-${index}`}
            style={{ width: "100%" }}
            data-testid={`asb-row-${index}`}
          >
            <Input
              style={{ width: "28%" }}
              placeholder={t("mcp_catalog.field_key_placeholder")}
              value={field.key}
              maxLength={64}
              data-testid={`asb-key-${index}`}
              onChange={(e) => update(index, { key: e.target.value })}
            />
            <Input
              style={{ width: "32%" }}
              placeholder={t("mcp_catalog.field_label_placeholder")}
              value={field.label}
              maxLength={128}
              data-testid={`asb-label-${index}`}
              onChange={(e) => update(index, { label: e.target.value })}
            />
            <Select<McpAuthFieldKind>
              style={{ width: "22%" }}
              value={field.kind}
              data-testid={`asb-kind-${index}`}
              options={KIND_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
              onChange={(kind) => update(index, { kind })}
            />
            <Button
              style={{ width: "10%", minWidth: 44 }}
              data-testid={`asb-required-${index}`}
              type={field.required ? "primary" : "default"}
              title={t("mcp_catalog.field_required")}
              onClick={() => update(index, { required: !field.required })}
            >
              <Checkbox
                checked={field.required}
                aria-label={t("mcp_catalog.field_required")}
                style={{ pointerEvents: "none" }}
              />
            </Button>
            <Button
              danger
              icon={<Trash2 size={14} strokeWidth={1.5} />}
              data-testid={`asb-remove-${index}`}
              aria-label={t("mcp_catalog.field_remove")}
              onClick={() => remove(index)}
            />
          </Space.Compact>
        ))}
        <Button
          icon={<Plus size={14} strokeWidth={1.5} />}
          data-testid="asb-add"
          onClick={add}
          block
        >
          {t("mcp_catalog.field_add")}
        </Button>
      </Space>
    </div>
  );
}
