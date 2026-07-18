import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { SandboxSection } from "../SandboxSection";
import type { AgentManifest } from "../../form_model";

function renderSection(
  formData: AgentManifest = {},
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(<SandboxSection formData={formData} onChange={onChange} />);
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

describe("SandboxSection", () => {
  it("renders the persistent_workspace FieldRow", () => {
    renderSection();
    expect(
      rowFor("sandbox.filesystem.persistent_workspace"),
    ).toBeInTheDocument();
  });

  it("turning the switch on writes spec.sandbox.filesystem.persistent_workspace=true", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = rowFor("sandbox.filesystem.persistent_workspace");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.sandbox?.filesystem?.persistent_workspace).toBe(true);
  });

  it("turning the switch back off (default) deletes the key but keeps filesystem: {} on an existing manifest", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { sandbox: { filesystem: { persistent_workspace: true } } },
    };
    renderSection(seed, onChange);

    const row = rowFor("sandbox.filesystem.persistent_workspace");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(
      last.spec?.sandbox?.filesystem?.persistent_workspace,
    ).toBeUndefined();
    expect(last.spec?.sandbox?.filesystem).toEqual({});
  });

  it("renders the platform-effective-values note", () => {
    renderSection();
    expect(screen.getByTestId("sandbox-platform-note")).toHaveTextContent(
      "Platform-effective values",
    );
  });

  it("renders the declarative-fields note", () => {
    renderSection();
    expect(screen.getByTestId("sandbox-declarative-note")).toHaveTextContent(
      "declarative",
    );
  });
});
