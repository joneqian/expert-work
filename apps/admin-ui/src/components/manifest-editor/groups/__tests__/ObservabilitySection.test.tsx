import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { ObservabilitySection } from "../ObservabilitySection";
import type { AgentManifest } from "../../form_model";

function renderSection(
  formData: AgentManifest = {},
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(
    <ObservabilitySection formData={formData} onChange={onChange} />,
  );
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

describe("ObservabilitySection", () => {
  it("renders the cache.enabled FieldRow", () => {
    renderSection();
    expect(rowFor("cache.enabled")).toBeInTheDocument();
  });

  it("turning the switch off writes spec.cache.enabled=false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = rowFor("cache.enabled");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.cache?.enabled).toBe(false);
  });

  it("turning the switch back on (default) deletes the cache block", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = { spec: { cache: { enabled: false } } };
    renderSection(seed, onChange);

    const row = rowFor("cache.enabled");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.cache).toBeUndefined();
  });

  it("renders the triggers-truth note", () => {
    renderSection();
    expect(screen.getByTestId("observability-triggers-note")).toHaveTextContent(
      "not wired up",
    );
  });

  it("renders the declarative-fields note", () => {
    renderSection();
    expect(
      screen.getByTestId("observability-declarative-note"),
    ).toHaveTextContent("declarative");
  });
});
