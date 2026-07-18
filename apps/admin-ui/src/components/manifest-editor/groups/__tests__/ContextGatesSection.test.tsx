import { describe, expect, it, vi } from "vitest";
import { render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { ContextGatesSection } from "../ContextGatesSection";
import type { AgentManifest } from "../../form_model";

// The 18 knobs across the four PolicySpec sub-blocks (tool_result_prune /
// working_memory / context_compression / tool_output_budget) — mirrors
// ``ContextGatesFields`` in form_model.ts.
const FIELD_IDS = [
  "policies.tool_result_prune.enabled",
  "policies.tool_result_prune.threshold_pct",
  "policies.tool_result_prune.recent_tool_results_kept",
  "policies.working_memory.enabled",
  "policies.working_memory.threshold_pct",
  "policies.working_memory.max_recent_turns",
  "policies.working_memory.keep_first_turn",
  "policies.context_compression.enabled",
  "policies.context_compression.threshold_pct",
  "policies.context_compression.head_keep",
  "policies.context_compression.tail_keep",
  "policies.context_compression.flush_before_compaction",
  "policies.context_compression.max_passes",
  "policies.context_compression.max_turns",
  "policies.context_compression.max_tokens",
  "policies.context_compression.pressure_feedback",
  "policies.context_compression.pressure_warn_pct",
  "policies.tool_output_budget.enabled",
];

function renderSection(
  formData: AgentManifest = {},
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(<ContextGatesSection formData={formData} onChange={onChange} />);
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

// Fields in a collapsed panel are mounted (``forceRender``) but not in the
// accessibility tree, so ``getByRole`` can't reach them until the panel is
// opened — matches how a real user would interact with the section. Matches
// on the panel's header text, which is unique among panel titles (never
// collides with a nested FieldRow's "Impact" expander text).
async function openPanel(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
): Promise<void> {
  const header = within(document.body)
    .getByText(label, { selector: ".ant-collapse-header-text" })
    .closest(".ant-collapse-header") as HTMLElement;
  await user.click(header);
}

describe("ContextGatesSection", () => {
  it("renders all 18 FieldRows across the four panels", () => {
    renderSection();
    for (const id of FIELD_IDS) {
      expect(rowFor(id)).toBeInTheDocument();
    }
  });

  it("expands only the first panel (结果修剪) by default; the other three stay collapsed", () => {
    const { container } = renderSection();
    // Scope to the SECTION's own top-level Collapse's direct-child panels —
    // each FieldRow with an impact note renders its own nested Collapse (the
    // "Impact" expander), so a bare ``.ant-collapse-header`` query over the
    // whole container would also pick up those 18 inner headers. (jsdom's
    // ``:scope`` combinator support is unreliable here, so walk the DOM
    // directly instead of ``:scope > …`` selectors.)
    const outer = container.querySelector(
      '[data-testid="context-gates-section"] > .ant-collapse',
    ) as HTMLElement;
    const panelItems = Array.from(outer.children).filter((el) =>
      el.classList.contains("ant-collapse-item"),
    );
    expect(panelItems).toHaveLength(4);
    const headers = panelItems.map((item) => item.children[0]);
    expect(headers[0]).toHaveAttribute("aria-expanded", "true");
    expect(headers[1]).toHaveAttribute("aria-expanded", "false");
    expect(headers[2]).toHaveAttribute("aria-expanded", "false");
    expect(headers[3]).toHaveAttribute("aria-expanded", "false");
  });

  it("changing the tool-result-prune threshold writes policies.tool_result_prune.threshold_pct", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = rowFor("policies.tool_result_prune.threshold_pct");
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "0.4");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_result_prune?.threshold_pct).toBe(0.4);
  });

  it("switching tool_result_prune.enabled off writes false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = rowFor("policies.tool_result_prune.enabled");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_result_prune?.enabled).toBe(false);
  });

  it("switching tool_result_prune.enabled back to true (=default) deletes the key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { policies: { tool_result_prune: { enabled: false } } },
    };
    renderSection(seed, onChange);

    const row = rowFor("policies.tool_result_prune.enabled");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_result_prune).toBeUndefined();
  });

  it("changing working_memory.max_recent_turns writes policies.working_memory.max_recent_turns", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "② Sliding window");

    const row = rowFor("policies.working_memory.max_recent_turns");
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "12");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.working_memory?.max_recent_turns).toBe(12);
  });

  it("changing context_compression.head_keep writes policies.context_compression.head_keep", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "③ Context compression");

    const row = rowFor("policies.context_compression.head_keep");
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "2");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.context_compression?.head_keep).toBe(2);
  });

  it("context_compression.max_turns has no numeric default (effectiveDefault null) — shows empty and writes an explicit value", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "③ Context compression");

    const row = rowFor("policies.context_compression.max_turns");
    const input = within(row).getByRole("spinbutton") as HTMLInputElement;
    expect(input).toHaveValue("");
    await user.type(input, "8");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.context_compression?.max_turns).toBe(8);
  });

  it("switching tool_output_budget.enabled off writes policies.tool_output_budget.enabled = false", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);
    await openPanel(user, "④ Tool-output budget");

    const row = rowFor("policies.tool_output_budget.enabled");
    await user.click(within(row).getByRole("switch"));

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_output_budget?.enabled).toBe(false);
  });

  it("patching one sub-block preserves sibling sub-blocks", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: {
        policies: {
          working_memory: { max_recent_turns: 30 },
          tool_output_budget: { enabled: false },
        },
      },
    };
    renderSection(seed, onChange);

    const row = rowFor("policies.tool_result_prune.threshold_pct");
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "0.5");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.tool_result_prune?.threshold_pct).toBe(0.5);
    expect(last.spec?.policies?.working_memory?.max_recent_turns).toBe(30);
    expect(last.spec?.policies?.tool_output_budget?.enabled).toBe(false);
  });

  it("shows the gray 'Default 0.7' badge for tool_result_prune.threshold_pct when unset", () => {
    renderSection({});
    const row = rowFor("policies.tool_result_prune.threshold_pct");
    expect(within(row).getByText("Default 0.7")).toBeInTheDocument();
  });
});
