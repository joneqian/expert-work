/**
 * Schema-driven form view — RJSF (antd theme) rendering the AgentSpec JSON
 * Schema. The whole manifest is editable here without custom code; ``uiSchema``
 * only nudges layout (collapse rarely-touched blocks, multiline the prompt).
 * The model picker stays on RJSF's default widgets in PR C — PR D swaps in a
 * provider/model linked widget.
 */
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import type { IChangeEvent } from "@rjsf/core";
import type { UiSchema } from "@rjsf/utils";

import type { JsonSchema } from "../../api/manifest_schema";

interface FormViewProps {
  schema: JsonSchema;
  formData: unknown;
  onChange: (data: unknown) => void;
}

/** Baseline layout polish. Keep minimal — PR D extends it for the model. */
const UI_SCHEMA: UiSchema = {
  "ui:submitButtonOptions": { norender: true },
  spec: {
    system_prompt: {
      template: { "ui:widget": "textarea", "ui:options": { rows: 6 } },
    },
  },
};

export function FormView({ schema, formData, onChange }: FormViewProps) {
  return (
    <div data-testid="manifest-form-view">
      <Form
        schema={schema}
        uiSchema={UI_SCHEMA}
        formData={formData}
        validator={validator}
        liveValidate={false}
        showErrorList={false}
        onChange={(e: IChangeEvent) => onChange(e.formData)}
      />
    </div>
  );
}
