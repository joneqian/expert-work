import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { MemorySection } from "../MemorySection";
import type { AgentManifest } from "../../form_model";

// The three new curated fields (injection budgets + consolidation switch) —
// mirrors ``MemoryBudgetFields``/``ConsolidationFields`` in form_model.ts.
const FIELD_IDS = [
  "memory.long_term.injection_token_budget",
  "memory.long_term.correction_token_budget",
  "policies.memory_consolidation.enabled",
];

// Memory on (a declared, possibly-empty ``long_term`` block) — required for
// the injection-budget panel to render at all (see the render-guard tests
// below for the off case).
const ON_SEED: AgentManifest = { spec: { memory: { long_term: {} } } };

function renderSection(
  formData: AgentManifest = ON_SEED,
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(<MemorySection formData={formData} onChange={onChange} />);
}

function rowFor(fieldId: string): HTMLElement | null {
  return document.querySelector(`[data-field-id="${fieldId}"]`);
}

// Fields in a collapsed panel are mounted (``forceRender``) but not in the
// accessibility tree, so ``getByRole`` can't reach them until the panel is
// opened — matches how a real user would interact with the section (mirrors
// ``SecuritySection.test.tsx``'s helper of the same shape).
async function openPanel(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
): Promise<void> {
  const header = within(document.body)
    .getByText(label, { selector: ".ant-collapse-header-text" })
    .closest(".ant-collapse-header") as HTMLElement;
  await user.click(header);
}

describe("MemorySection", () => {
  it("embeds the existing 'memory' FormView section (af-memory-toggle always visible, af-memory-recall-mode once memory + the inner Advanced panel are open)", async () => {
    const user = userEvent.setup();
    renderSection();
    expect(screen.getByTestId("af-memory-toggle")).toBeInTheDocument();
    // af-memory-recall-mode lives inside FormView's own nested "Advanced"
    // collapse (no forceRender there), so it isn't in the accessibility
    // tree until that panel is opened.
    await openPanel(user, "Advanced");
    expect(screen.getByTestId("af-memory-recall-mode")).toBeInTheDocument();
  });

  it("renders the three new FieldRows (injection budgets + consolidation) when memory is on", () => {
    renderSection();
    for (const id of FIELD_IDS) {
      expect(rowFor(id)).toBeInTheDocument();
    }
  });

  it("both new panels start collapsed", () => {
    const { container } = renderSection();
    const outer = container.querySelector(
      '[data-testid="memory-section"] > .ant-collapse',
    ) as HTMLElement;
    const panelItems = Array.from(outer.children).filter((el) =>
      el.classList.contains("ant-collapse-item"),
    );
    expect(panelItems).toHaveLength(2);
    const headers = panelItems.map((item) => item.children[0]);
    expect(headers[0]).toHaveAttribute("aria-expanded", "false");
    expect(headers[1]).toHaveAttribute("aria-expanded", "false");
  });

  it("does not render the injection-budget panel when memory is off (long_term: null) — the consolidation panel still does", () => {
    const OFF_SEED: AgentManifest = { spec: { memory: { long_term: null } } };
    const { container } = renderSection(OFF_SEED);

    expect(
      rowFor("memory.long_term.injection_token_budget"),
    ).not.toBeInTheDocument();
    expect(
      rowFor("memory.long_term.correction_token_budget"),
    ).not.toBeInTheDocument();
    expect(rowFor("policies.memory_consolidation.enabled")).toBeInTheDocument();

    const outer = container.querySelector(
      '[data-testid="memory-section"] > .ant-collapse',
    ) as HTMLElement;
    const panelItems = Array.from(outer.children).filter((el) =>
      el.classList.contains("ant-collapse-item"),
    );
    expect(panelItems).toHaveLength(1);
  });

  it("editing injection budget to 3000 writes memory.long_term.injection_token_budget", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection(ON_SEED, onChange);
    await openPanel(user, "① Injection budget");

    const input = within(
      rowFor("memory.long_term.injection_token_budget") as HTMLElement,
    ).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "3000");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.memory?.long_term?.injection_token_budget).toBe(3000);
  });

  it("turning consolidation off writes policies.memory_consolidation.enabled = false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection(ON_SEED, onChange);
    await openPanel(user, "② Background memory consolidation");

    const switchEl = within(
      rowFor("policies.memory_consolidation.enabled") as HTMLElement,
    ).getByRole("switch");
    await user.click(switchEl);

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.memory_consolidation?.enabled).toBe(false);
  });

  it("toggling consolidation back to true (the default) deletes the key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: {
        memory: { long_term: {} },
        policies: { memory_consolidation: { enabled: false } },
      },
    };
    renderSection(seed, onChange);
    await openPanel(user, "② Background memory consolidation");

    const switchEl = within(
      rowFor("policies.memory_consolidation.enabled") as HTMLElement,
    ).getByRole("switch");
    await user.click(switchEl);

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.memory_consolidation).toBeUndefined();
  });

  it("renders the closing reserved-fields note", () => {
    renderSection();
    expect(screen.getByTestId("memory-reserved-note")).toHaveTextContent(
      "memory.short_term and dynamic_context.inject_memory are currently reserved fields",
    );
  });
});
