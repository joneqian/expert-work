/**
 * BasicSection / RunProfileCard — config-page redesign v2 Task 6. The card's
 * contract: reflect the CURRENT manifest (inferRunProfile — checked radio or
 * a "Custom" tag), and gate a lossy preset apply behind Modal.confirm when
 * countProfileDiff > 0 — cancelling the dialog must apply NOTHING (the
 * mutation-proof case: onChange not called).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../../i18n";

import { BasicSection } from "../BasicSection";
import { applyRunProfile, readRunBudget, type AgentManifest } from "../../form_model";
import { parseYaml } from "../../yaml";
import { BASE_MANIFEST_YAML } from "../../defaults";

// The new-agent seed infers as "balanced" (memory on, max_no_progress 4).
const SEED = (): AgentManifest => parseYaml(BASE_MANIFEST_YAML) as AgentManifest;

function renderSection(
  formData: AgentManifest,
  onChange: (d: unknown) => void = vi.fn(),
) {
  // RunProfileCard confirms via App.useApp()'s modal — the antd <App>
  // provider is part of the component's contract (same wrapping the real
  // app root provides).
  return render(
    <App>
      <BasicSection formData={formData} onChange={onChange} />
    </App>,
  );
}

afterEach(() => {
  // the confirm dialog portals outside the RTL container — drop leftovers
  // so one test's dialog can't satisfy the next test's queries.
  document.body.innerHTML = "";
});

describe("BasicSection / RunProfileCard", () => {
  it("renders the three profile radios and the basic FormView section", () => {
    renderSection(SEED());
    expect(screen.getByTestId("run-profile-card")).toBeInTheDocument();
    expect(screen.getByTestId("run-profile-balanced")).toBeInTheDocument();
    expect(screen.getByTestId("run-profile-cost")).toBeInTheDocument();
    expect(screen.getByTestId("run-profile-capability")).toBeInTheDocument();
    // the plain basic section rides below the card
    expect(screen.getByTestId("af-basic")).toBeInTheDocument();
  });

  it("infers the seed as 'balanced' (radio checked, no Custom tag)", () => {
    renderSection(SEED());
    expect(screen.getByRole("radio", { name: /Balanced/ })).toBeChecked();
    expect(screen.queryByTestId("run-profile-custom-tag")).toBeNull();
  });

  it("shows the Custom tag with no radio checked once a managed field drifts", () => {
    const drifted = { spec: { policies: { max_no_progress: 99 } } };
    renderSection(drifted);
    expect(screen.getByTestId("run-profile-custom-tag")).toBeInTheDocument();
    for (const name of [/Balanced/, /Cost-saving/, /High-capability/]) {
      expect(screen.getByRole("radio", { name })).not.toBeChecked();
    }
  });

  it("picking a different profile confirms first, then applies via applyRunProfile", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seed = SEED();
    renderSection(seed, onChange);

    await user.click(screen.getByTestId("run-profile-cost"));
    // confirm dialog names the profile
    await waitFor(() =>
      expect(screen.getAllByText('Apply "Cost-saving"?').length).toBeGreaterThan(0),
    );
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const applied = onChange.mock.calls[0][0] as AgentManifest;
    // the payload IS applyRunProfile's output (spot-check a cost value)
    expect(readRunBudget(applied).maxIterations).toBe(20);
    expect(applied).toEqual(applyRunProfile(seed, "cost"));
  });

  it("cancelling the confirm dialog applies nothing (onChange never called)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderSection(SEED(), onChange);

    await user.click(screen.getByTestId("run-profile-capability"));
    await waitFor(() =>
      expect(
        screen.getAllByText('Apply "High-capability"?').length,
      ).toBeGreaterThan(0),
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryAllByText('Apply "High-capability"?')).toHaveLength(0),
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});
