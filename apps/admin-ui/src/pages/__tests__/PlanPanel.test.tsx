/**
 * PlanPanel tests — Stream CM-8 PR3.
 *
 * ``getThreadPlan`` / ``updateThreadPlan`` are spied on so each test
 * verifies the read view, the structured edit flow, and the
 * live-run lock independent of the network.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import * as planSdk from "../../api/plan";
import { PlanPanel } from "../run_detail/PlanPanel";
import type { ThreadPlan } from "../../api/plan";

const getPlanMock = vi.spyOn(planSdk, "getThreadPlan");
const updatePlanMock = vi.spyOn(planSdk, "updateThreadPlan");

beforeEach(() => {
  getPlanMock.mockReset();
  updatePlanMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

const plan: ThreadPlan = {
  goal: "ship the feature",
  steps: [
    { id: "1", description: "write tests", status: "completed" },
    { id: "2", description: "implement", status: "in_progress" },
  ],
};

function renderPanel(runStatus: string | null = "success") {
  return render(
    <App>
      <PlanPanel threadId="t-1" runStatus={runStatus} />
    </App>,
  );
}

describe("PlanPanel", () => {
  it("renders the goal, steps and progress in the read view", async () => {
    getPlanMock.mockResolvedValue(plan);
    renderPanel();
    expect(await screen.findByText("ship the feature")).toBeInTheDocument();
    expect(screen.getByText("write tests")).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
    expect(screen.getByText("1/2 completed")).toBeInTheDocument();
  });

  it("renders the empty state when the thread has no plan", async () => {
    getPlanMock.mockResolvedValue(null);
    renderPanel();
    expect(await screen.findByTestId("plan-empty")).toBeInTheDocument();
    expect(screen.getByText("The agent has not made a plan yet.")).toBeInTheDocument();
  });

  it("disables Edit while the run is live", async () => {
    getPlanMock.mockResolvedValue(plan);
    renderPanel("running");
    await screen.findByText("ship the feature");
    expect(screen.getByTestId("plan-edit")).toBeDisabled();
  });

  it("edits a step and submits the full plan shape", async () => {
    getPlanMock.mockResolvedValue(plan);
    const stored: ThreadPlan = {
      goal: "ship the feature",
      steps: [
        { id: "1", description: "write tests", status: "completed" },
        { id: "2", description: "implement", status: "completed" },
      ],
    };
    updatePlanMock.mockResolvedValue(stored);
    renderPanel();
    await screen.findByText("ship the feature");

    await userEvent.click(screen.getByTestId("plan-edit"));
    // Flip step 2 to completed via the structured form. AntD Select
    // opens on mousedown (not click) and portals its options.
    const select = screen.getByTestId("plan-step-status-1");
    fireEvent.mouseDown(select.querySelector(".ant-select-selector") ?? select);
    await userEvent.click(
      await screen.findByText("completed", { selector: ".ant-select-item-option-content" }),
    );
    await userEvent.click(screen.getByTestId("plan-save"));

    await waitFor(() => expect(updatePlanMock).toHaveBeenCalledTimes(1));
    const [threadId, payload] = updatePlanMock.mock.calls[0];
    expect(threadId).toBe("t-1");
    expect(payload.steps.map((s) => s.status)).toEqual(["completed", "completed"]);
    // The stored echo replaces the read view.
    expect(await screen.findByTestId("plan-read-view")).toBeInTheDocument();
  });

  it("adds and removes steps in edit mode and blocks empty drafts", async () => {
    getPlanMock.mockResolvedValue(plan);
    renderPanel();
    await screen.findByText("ship the feature");
    await userEvent.click(screen.getByTestId("plan-edit"));

    await userEvent.click(screen.getByTestId("plan-add-step"));
    // New step is empty — Save must be disabled until it has text.
    expect(screen.getByTestId("plan-save")).toBeDisabled();
    await userEvent.type(screen.getByTestId("plan-step-input-2"), "review");
    expect(screen.getByTestId("plan-save")).not.toBeDisabled();

    await userEvent.click(screen.getByTestId("plan-step-remove-2"));
    expect(screen.queryByTestId("plan-step-input-2")).not.toBeInTheDocument();
  });

  it("cancel restores the read view without a write", async () => {
    getPlanMock.mockResolvedValue(plan);
    renderPanel();
    await screen.findByText("ship the feature");
    await userEvent.click(screen.getByTestId("plan-edit"));
    await userEvent.type(screen.getByTestId("plan-goal-input"), " — edited");
    await userEvent.click(screen.getByTestId("plan-cancel-edit"));
    expect(await screen.findByTestId("plan-read-view")).toBeInTheDocument();
    expect(updatePlanMock).not.toHaveBeenCalled();
  });
});
