/**
 * OutputSchemaEditor — config-page redesign v2 Task 7. Contract under test:
 *  - off by default, on writes the empty-object default schema.
 *  - editing rows emits the exact ``setOutputSchemaRows``-shaped manifest.
 *  - an invalid field name blocks the commit (no ``onChange``) and shows an
 *    error state on the Input — the mutation-proof case for "非法名不写入
 *    manifest".
 *  - an "unrepresentable" (non-flat) schema renders read-only: the Switch is
 *    hidden, only an info Alert shows, and NOTHING in that state ever calls
 *    ``onChange`` (there is nothing interactive to click).
 *  - turning off with existing fields confirms first (App.useApp() modal,
 *    not the static Modal.confirm — renders under test); cancelling applies
 *    nothing.
 */
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../../i18n";

import { OutputSchemaEditor } from "../OutputSchemaEditor";
import type { AgentManifest } from "../../form_model";

function renderEditor(
  formData: unknown = { spec: {} },
  onChange: (d: unknown) => void = vi.fn(),
) {
  // The Switch's off-with-existing-fields confirm rides App.useApp()'s modal
  // — the antd <App> provider is part of the component's contract (same
  // wrapping the real app root provides; mirrors BasicSection's RunProfileCard).
  return render(
    <App>
      <OutputSchemaEditor formData={formData} onChange={onChange} />
    </App>,
  );
}

// A real controlled wrapper (state feeds back into ``formData``) — needed for
// multi-step flows so the editor's own resync-from-props effect is exercised
// the same way it is under the real ``ManifestEditor`` parent, rather than
// relying solely on the widget's local row state to carry the UI forward.
function renderControlled(initial: unknown, onChangeSpy: (d: unknown) => void) {
  function Harness() {
    const [data, setData] = useState<unknown>(initial);
    const handleChange = (next: unknown): void => {
      setData(next);
      onChangeSpy(next);
    };
    return (
      <App>
        <OutputSchemaEditor formData={data} onChange={handleChange} />
      </App>
    );
  }
  return render(<Harness />);
}

afterEach(() => {
  // the confirm dialog portals outside the RTL container.
  document.body.innerHTML = "";
});

describe("OutputSchemaEditor", () => {
  it("renders off with the hint when not configured", () => {
    renderEditor();
    expect(screen.getByTestId("af-output-schema-switch")).not.toBeChecked();
    expect(screen.queryByTestId("af-output-schema-row-0")).not.toBeInTheDocument();
  });

  it("turning the switch on writes the empty-object default schema", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderEditor({ spec: {} }, onChange);
    await user.click(screen.getByTestId("af-output-schema-switch"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as AgentManifest;
    expect(next.spec?.output_schema).toEqual({
      json_schema: { type: "object", properties: {}, additionalProperties: false },
    });
  });

  it("turning the switch off with no fields deletes the block without confirming", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          json_schema: { type: "object", properties: {}, additionalProperties: false },
        },
      },
    };
    renderEditor(seeded, onChange);
    await user.click(screen.getByTestId("af-output-schema-switch"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as AgentManifest;
    expect(next.spec?.output_schema).toBeUndefined();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("adding two fields and naming them produces the exact json_schema shape", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderControlled({ spec: {} }, onChange);

    // turn on
    await user.click(screen.getByTestId("af-output-schema-switch"));

    // add field 1, name it "title", mark required
    await user.click(screen.getByTestId("af-output-schema-add"));
    await user.type(screen.getByTestId("af-output-schema-name-0"), "title");
    await user.click(screen.getByTestId("af-output-schema-required-0"));

    // add field 2, name it "count" (default type: string)
    await user.click(screen.getByTestId("af-output-schema-add"));
    await user.type(screen.getByTestId("af-output-schema-name-1"), "count");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.output_schema?.json_schema).toEqual({
      type: "object",
      properties: {
        title: { type: "string" },
        count: { type: "string" },
      },
      required: ["title"],
      additionalProperties: false,
    });
  });

  it("an invalid field name blocks the commit and shows an error state", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { title: { type: "string" } },
            additionalProperties: false,
          },
        },
      },
    };
    renderEditor(seeded, onChange);
    const nameInput = screen.getByTestId("af-output-schema-name-0");
    await user.clear(nameInput);
    onChange.mockClear();
    await user.type(nameInput, "1bad");
    expect(onChange).not.toHaveBeenCalled();
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
  });

  it("removing the invalid-name row un-blocks (no leftover invalid field)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { title: { type: "string" } },
            additionalProperties: false,
          },
        },
      },
    };
    renderEditor(seeded, onChange);
    await user.clear(screen.getByTestId("af-output-schema-name-0"));
    onChange.mockClear();
    await user.click(screen.getByTestId("af-output-schema-remove-0"));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as AgentManifest;
    expect(next.spec?.output_schema?.json_schema).toEqual({
      type: "object",
      properties: {},
      additionalProperties: false,
    });
  });

  it("turning off with existing fields confirms first; OK deletes the block", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { title: { type: "string" } },
            additionalProperties: false,
          },
        },
      },
    };
    renderEditor(seeded, onChange);
    await user.click(screen.getByTestId("af-output-schema-switch"));
    expect(onChange).not.toHaveBeenCalled();

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "OK" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0] as AgentManifest;
    expect(next.spec?.output_schema).toBeUndefined();
  });

  it("cancelling the off-confirm applies nothing", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { title: { type: "string" } },
            additionalProperties: false,
          },
        },
      },
    };
    renderEditor(seeded, onChange);
    await user.click(screen.getByTestId("af-output-schema-switch"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("preserves an existing name/strict when editing rows", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded = {
      spec: {
        output_schema: {
          name: "custom",
          strict: false,
          json_schema: {
            type: "object",
            properties: { title: { type: "string" } },
            additionalProperties: false,
          },
        },
      },
    };
    renderEditor(seeded, onChange);
    await user.type(screen.getByTestId("af-output-schema-desc-0"), "hi");
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.output_schema?.name).toBe("custom");
    expect(last.spec?.output_schema?.strict).toBe(false);
  });

  describe("unrepresentable schema", () => {
    const seeded = {
      spec: {
        output_schema: {
          json_schema: {
            type: "object",
            properties: { nested: { type: "object", properties: {} } },
          },
        },
      },
    };

    it("shows a read-only alert and hides the switch", () => {
      renderEditor(seeded);
      expect(screen.getByTestId("af-output-schema-readonly")).toBeInTheDocument();
      expect(
        screen.queryByTestId("af-output-schema-switch"),
      ).not.toBeInTheDocument();
    });

    it("has nothing clickable that produces onChange (mutation-proof)", async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor(seeded, onChange);
      const alert = screen.getByTestId("af-output-schema-readonly");
      await user.click(alert);
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.queryByTestId("af-output-schema-add")).not.toBeInTheDocument();
      expect(screen.queryByTestId("af-output-schema-row-0")).not.toBeInTheDocument();
    });
  });
});
