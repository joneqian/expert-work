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
  it("renders all seven fields, all visible", () => {
    const { container } = renderSection();
    for (const id of FIELD_IDS) {
      const row = container.querySelector(`[data-field-id="${id}"]`);
      expect(row).toBeInTheDocument();
      expect(row).toBeVisible();
    }
  });

  it("renders two subheads splitting steps/flow from time/spend", () => {
    renderSection();
    expect(screen.getByText("Steps & flow")).toBeInTheDocument();
    expect(screen.getByText("Time & spend")).toBeInTheDocument();
  });

  it("groups max_iterations/workflow.type/max_no_progress under the steps subhead, and the rest under time", () => {
    const { container } = renderSection();
    const stepsHeading = screen.getByText("Steps & flow");
    const timeHeading = screen.getByText("Time & spend");

    for (const id of [
      "workflow.max_iterations",
      "workflow.type",
      "policies.max_no_progress",
    ]) {
      const row = container.querySelector(`[data-field-id="${id}"]`) as HTMLElement;
      expect(
        stepsHeading.compareDocumentPosition(row) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
      expect(
        timeHeading.compareDocumentPosition(row) &
          Node.DOCUMENT_POSITION_PRECEDING,
      ).toBeTruthy();
    }

    for (const id of [
      "policies.run_deadline_s",
      "policies.token_budget",
      "spec.stream_deadline_s",
      "spec.idle_timeout_s",
    ]) {
      const row = container.querySelector(`[data-field-id="${id}"]`) as HTMLElement;
      expect(
        timeHeading.compareDocumentPosition(row) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it("no longer renders the removed workflow reserved-fields note", () => {
    renderSection();
    expect(screen.queryByTestId("budget-workflow-note")).not.toBeInTheDocument();
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

  it("shows no '已自定义' tag when a field is unset (at default)", () => {
    renderSection({});
    expect(
      screen.queryByTestId("field-customized-workflow.max_iterations"),
    ).not.toBeInTheDocument();
  });

  it("shows the '已自定义' tag and a reset button once a field diverges from default", () => {
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 60 } },
    };
    renderSection(seed);
    expect(
      screen.getByTestId("field-customized-workflow.max_iterations"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("field-reset-workflow.max_iterations"),
    ).toBeInTheDocument();
  });

  it("clicking reset on a diverged field reverts it to the platform default", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed: AgentManifest = {
      spec: { workflow: { max_iterations: 60 } },
    };
    renderSection(seed, onChange);

    await user.click(
      screen.getByTestId("field-reset-workflow.max_iterations"),
    );

    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.workflow?.max_iterations).toBeUndefined();
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
