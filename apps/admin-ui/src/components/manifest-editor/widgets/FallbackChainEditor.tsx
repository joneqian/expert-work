/**
 * FallbackChainEditor — edits the main model's E.11 provider fallback chain
 * (``spec.model.fallback``). A flat, ordered list: the router tries the primary
 * first, then each fallback in turn (a slow / failing provider falls over
 * instead of killing the run). Each entry reuses ``ModelSelect``; an empty list
 * writes no ``fallback`` block (single-provider agent).
 */
import { Button, Empty } from "antd";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ModelCatalog } from "../../../api/model_catalog";
import type { ModelFields } from "../form_model";
import { ModelSelect } from "./ModelSelect";

interface FallbackChainEditorProps {
  value: ModelFields[];
  catalog?: ModelCatalog;
  onChange: (next: ModelFields[]) => void;
}

export function FallbackChainEditor({
  value,
  catalog,
  onChange,
}: FallbackChainEditorProps) {
  const { t } = useTranslation();

  const updateAt = (i: number, next: ModelFields): void => {
    onChange(value.map((entry, idx) => (idx === i ? next : entry)));
  };
  const removeAt = (i: number): void => {
    onChange(value.filter((_, idx) => idx !== i));
  };
  const add = (): void => {
    onChange([...value, {}]);
  };

  return (
    <div data-testid="af-fallback-chain">
      {value.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("agent_form.fallback_empty")}
          style={{ margin: "8px 0" }}
        />
      ) : (
        value.map((entry, i) => (
          // Index key is safe: ModelSelect is fully controlled (no internal
          // state to mis-associate when an entry is removed).
          <div
            key={i}
            data-testid={`af-fallback-entry-${i}`}
            style={{
              border: "1px solid var(--ew-border-default)",
              borderRadius: 6,
              padding: 12,
              marginBottom: 8,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              }}
            >
              <strong>{t("agent_form.fallback_rank", { n: i + 1 })}</strong>
              <Button
                type="text"
                danger
                size="small"
                icon={<Trash2 size={14} strokeWidth={1.75} />}
                data-testid={`af-fallback-remove-${i}`}
                onClick={() => removeAt(i)}
              >
                {t("agent_form.fallback_remove")}
              </Button>
            </div>
            <ModelSelect
              value={entry}
              catalog={catalog}
              onChange={(mdl) => updateAt(i, mdl)}
            />
          </div>
        ))
      )}
      <Button
        type="dashed"
        block
        icon={<Plus size={14} strokeWidth={1.75} />}
        data-testid="af-fallback-add"
        onClick={add}
      >
        {t("agent_form.fallback_add")}
      </Button>
    </div>
  );
}
