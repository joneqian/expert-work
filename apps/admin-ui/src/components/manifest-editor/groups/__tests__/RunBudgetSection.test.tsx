import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { RunBudgetSection } from "../RunBudgetSection";
import type { AgentManifest } from "../../form_model";

const FIELD_IDS = [
  "workflow.max_iterations",
  "workflow.type",
  "policies.max_no_progress",
  "policies.run_deadline_s",
  "policies.token_budget",
  "spec.stream_deadline_s",
  "spec.idle_timeout_s",
];

function renderSection(
  formData: AgentManifest = {},
  onChange: (d: unknown) => void = vi.fn(),
) {
  return render(<RunBudgetSection formData={formData} onChange={onChange} />);
}

function rowFor(fieldId: string): HTMLElement {
  return document.querySelector(`[data-field-id="${fieldId}"]`) as HTMLElement;
}

/**
 * In jsdom, Antd's Select renders each option twice: a visible, clickable
 * ``.ant-select-item-option`` div and a hidden ARIA ``role="option"`` mirror
 * with the same text. This opens the given combobox and clicks the real
 * ``.ant-select-item-option-content`` carrying the requested label (mirrors
 * ``SecuritySection.test.tsx``'s helper of the same shape).
 */
async function pickOption(
  user: ReturnType<typeof userEvent.setup>,
  combobox: HTMLElement,
  label: string,
): Promise<void> {
  await user.click(combobox);
  const item = await screen.findByText(optionContent(label));
  await user.click(item);
}

const optionContent =
  (label: string) => (_content: string, el: Element | null) =>
    el?.classList.contains("ant-select-item-option-content") === true &&
    el.textContent === label;

describe("RunBudgetSection", () => {
  it("renders as a PolicyFieldTable with all seven fields as visible table rows", () => {
    const { container } = renderSection();
    expect(screen.getByTestId("policy-field-table")).toBeInTheDocument();
    for (const id of FIELD_IDS) {
      const row = container.querySelector(`[data-field-id="${id}"]`);
      expect(row).toBeInTheDocument();
      expect(row).toBeVisible();
    }
  });

  it("workflow.type select renders with 3 options", async () => {
    const user = userEvent.setup();
    renderSection();
    const combobox = within(rowFor("workflow.type")).getByRole("combobox");
    await user.click(combobox);
    expect(
      await screen.findByText(optionContent("react (think, then act)")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(optionContent("plan_execute (plan, then execute)")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(optionContent("custom (deprecated — same as react)")),
    ).toBeInTheDocument();
  });

  it("picking plan_execute writes spec.workflow.type", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const combobox = within(rowFor("workflow.type")).getByRole("combobox");
    await pickOption(user, combobox, "plan_execute (plan, then execute)");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.type).toBe("plan_execute");
  });

  it("picking react (the default) back deletes workflow.type but keeps max_iterations", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 40, type: "plan_execute" } },
    };
    renderSection(seed, onChange);

    const combobox = within(rowFor("workflow.type")).getByRole("combobox");
    await pickOption(user, combobox, "react (think, then act)");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.type).toBeUndefined();
    expect(last.spec?.workflow?.max_iterations).toBe(40);
  });

  it("renders the workflow reserved-fields note", () => {
    renderSection();
    expect(screen.getByTestId("budget-workflow-note")).toBeInTheDocument();
  });

  it("changing max_iterations writes spec.workflow.max_iterations", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = document.querySelector(
      '[data-field-id="workflow.max_iterations"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "45");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.max_iterations).toBe(45);
  });

  it("clearing max_iterations deletes the key (revert to default)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 45 } },
    };
    renderSection(seed, onChange);

    const row = document.querySelector(
      '[data-field-id="workflow.max_iterations"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.max_iterations).toBeUndefined();
  });

  it("shows the gray 'Default <value>' badge when a field is unset", () => {
    renderSection({});
    // max_iterations defaults to 30 when unset.
    expect(screen.getByText("Default 30")).toBeInTheDocument();
  });

  it("shows a blue current-value badge once a field diverges from default", () => {
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 60 } },
    };
    renderSection(seed);
    expect(screen.queryByText("Default 30")).not.toBeInTheDocument();
    const badge = screen.getByText("60");
    expect(badge.closest(".ant-tag")).toHaveClass("ant-tag-blue");
  });

  it("changing max_no_progress writes spec.policies.max_no_progress", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = document.querySelector(
      '[data-field-id="policies.max_no_progress"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "3");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.max_no_progress).toBe(3);
  });

  it("changing run_deadline_s writes spec.policies.run_deadline_s", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = document.querySelector(
      '[data-field-id="policies.run_deadline_s"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "1800");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.policies?.run_deadline_s).toBe(1800);
  });

  it("changing stream_deadline_s writes top-level spec.stream_deadline_s", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = document.querySelector(
      '[data-field-id="spec.stream_deadline_s"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "90");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.stream_deadline_s).toBe(90);
  });

  it("changing idle_timeout_s writes top-level spec.idle_timeout_s", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection({}, onChange);

    const row = document.querySelector(
      '[data-field-id="spec.idle_timeout_s"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "20");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.idle_timeout_s).toBe(20);
  });

  it("preserves sibling workflow keys (early_stop/builder) when patching max_iterations", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 30, builder: "custom" } },
    };
    renderSection(seed, onChange);

    const row = document.querySelector(
      '[data-field-id="workflow.max_iterations"]',
    ) as HTMLElement;
    const input = within(row).getByRole("spinbutton");
    await user.clear(input);
    await user.type(input, "50");

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.max_iterations).toBe(50);
    expect(last.spec?.workflow?.builder).toBe("custom");
  });
});
