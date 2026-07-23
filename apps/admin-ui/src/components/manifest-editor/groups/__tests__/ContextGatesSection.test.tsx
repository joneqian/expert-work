import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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

// The four PolicyFieldTable group-header rows ContextGatesSection wires one
// per PolicySpec sub-block — mirrors the ``context_gates.panel_*`` i18n
// values (en locale).
const GROUP_TITLES = [
  "① Tool-result prune",
  "② Sliding window",
  "③ Context compression",
  "④ Tool-output budget",
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

describe("ContextGatesSection", () => {
  it("renders as a single PolicyFieldTable — no Collapse anywhere", () => {
    renderSection();
    expect(screen.getByTestId("policy-field-table")).toBeInTheDocument();
    expect(document.querySelector(".ant-collapse")).not.toBeInTheDocument();
  });

  it("renders all four group titles and all 18 fields, all visible with no expand step", () => {
    renderSection();
    for (const title of GROUP_TITLES) {
      expect(screen.getByText(title)).toBeVisible();
    }
    for (const id of FIELD_IDS) {
      const row = rowFor(id);
      expect(row).toBeInTheDocument();
      expect(row).toBeVisible();
    }
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
