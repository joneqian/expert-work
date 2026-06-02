import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";

import "../../../i18n";
import { ModelSelectField } from "../widgets/ModelSelectField";

/**
 * In jsdom, Antd's Select renders each option twice: a visible, clickable
 * `.ant-select-item-option` div and a hidden ARIA `role="option"` mirror with
 * the same text. `findByText`/`findByRole("option")` therefore match the wrong
 * (non-clickable) node. This helper opens the given combobox and clicks the
 * real `.ant-select-item-option` carrying the requested label.
 */
async function pickOption(
  user: ReturnType<typeof userEvent.setup>,
  combobox: HTMLElement,
  label: string,
): Promise<void> {
  await user.click(combobox);
  const item = await screen.findByText(
    (_content, el) =>
      el?.classList.contains("ant-select-item-option-content") === true &&
      el.textContent === label,
  );
  await user.click(item);
}

const MODELSPEC_SCHEMA = {
  type: "object",
  properties: {
    provider: { type: "string" },
    name: { type: "string" },
    supports_vision: { type: "boolean" },
    temperature: { type: "number", default: 0.2 },
  },
} as const;

const CATALOG = {
  providers: [
    { provider: "deepseek", models: [{ name: "deepseek-v4-pro", vision: false, embeddings: false, context_window: 1000000, deprecated: false }] },
    { provider: "openai", models: [
      { name: "gpt-5.5", vision: true, embeddings: false, context_window: 128000, deprecated: false },
      { name: "text-embedding-3-large", vision: false, embeddings: true, context_window: null, deprecated: false },
    ] },
  ],
};

function renderField(formData: unknown, onChange = vi.fn()) {
  return render(
    <Form
      schema={MODELSPEC_SCHEMA as object}
      validator={validator}
      fields={{ ModelSelect: ModelSelectField }}
      uiSchema={{ "ui:field": "ModelSelect", "ui:submitButtonOptions": { norender: true } }}
      formData={formData}
      formContext={{ modelCatalog: CATALOG }}
      onChange={(e) => onChange(e.formData)}
    />,
  );
}

describe("ModelSelectField", () => {
  it("lists configured providers", async () => {
    const user = userEvent.setup();
    renderField({});
    const provider = within(screen.getByTestId("model-select-provider")).getByRole("combobox");
    await user.click(provider);
    // Match the visible `.ant-select-item-option-content` nodes (see pickOption
    // note) — `findByText`/`role=option` match Antd's hidden a11y mirror twice.
    const optionContent = (label: string) =>
      screen.findByText(
        (_c, el) =>
          el?.classList.contains("ant-select-item-option-content") === true &&
          el.textContent === label,
      );
    expect(await optionContent("deepseek")).toBeInTheDocument();
    expect(await optionContent("openai")).toBeInTheDocument();
  });

  it("selecting a vision model auto-sets supports_vision", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderField({ provider: "openai" }, onChange);
    const nameSel = within(screen.getByTestId("model-select-name")).getByRole("combobox");
    await pickOption(user, nameSel, "gpt-5.5");
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "openai", name: "gpt-5.5", supports_vision: true }),
    );
  });

  it("shows the no-embeddings note for a provider without an embedding model", () => {
    renderField({ provider: "deepseek" });
    expect(screen.getByTestId("model-select-no-embeddings")).toBeInTheDocument();
  });

  it("keeps a remaining ModelSpec field (temperature) editable", () => {
    renderField({ provider: "openai", name: "gpt-5.5" });
    expect(screen.getByText(/temperature/i)).toBeInTheDocument();
  });

  it("renders translated labels (not raw i18n keys)", () => {
    renderField({ provider: "deepseek", name: "deepseek-v4-pro", supports_vision: false });
    // vision Tag uses a real translation, not the literal key
    expect(screen.queryByText("model_select.vision_off")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-select-vision")).toHaveTextContent(/视觉|Vision/);
  });
});
