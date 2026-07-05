/**
 * AgentDetail kill switch — Stream RT-4 (RT-ADR-16) PR-2.
 *
 * The overview tab of AgentDetail carries the agent-level disable/enable
 * control (mirrors the tenant suspend Popconfirm on SettingsTenants). These
 * tests render the real page (route params + getAgent fetch) so the
 * detail→status-tag→SDK wiring is covered end to end. Only the ``overview``
 * tab is exercised, so the per-tab list SDKs never load.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App } from "antd";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { AgentDetail } from "../AgentDetail";
import type { AgentDetailResponse } from "../../api/agents";
import { disableAgent, enableAgent, getAgent } from "../../api/agents";

vi.mock("../../api/agents", async () => {
  const actual = await vi.importActual<typeof import("../../api/agents")>("../../api/agents");
  return {
    ...actual,
    getAgent: vi.fn(),
    disableAgent: vi.fn(),
    enableAgent: vi.fn(),
  };
});

function detail(overrides: Partial<AgentDetailResponse> = {}): AgentDetailResponse {
  return {
    record: {
      id: "11111111-1111-1111-1111-111111111111",
      tenant_id: "22222222-2222-2222-2222-222222222222",
      name: "code-reviewer",
      version: "1.0.0",
      status: "active",
      spec_sha256: "a".repeat(64),
      created_by: "user-1",
      created_at: "2026-06-12T00:00:00Z",
      updated_at: "2026-06-12T00:00:00Z",
      spec: {},
    },
    disabled: false,
    disable: null,
    ...overrides,
  } as AgentDetailResponse;
}

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/agents/code-reviewer/1.0.0/overview"]}>
      <App>
        <Routes>
          <Route path="/agents/:name/:version/:tab" element={<AgentDetail />} />
        </Routes>
      </App>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("AgentDetail kill switch", () => {
  it("shows a Disable button and no disabled tag for an enabled agent", async () => {
    vi.mocked(getAgent).mockResolvedValue(detail());
    renderPage();

    expect(await screen.findByTestId("agent-disable-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-disabled-tag")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-enable-btn")).not.toBeInTheDocument();
  });

  it("shows the disabled tag and an Enable button for a disabled agent", async () => {
    vi.mocked(getAgent).mockResolvedValue(
      detail({
        disabled: true,
        disable: {
          tenant_id: "22222222-2222-2222-2222-222222222222",
          agent_name: "code-reviewer",
          disabled: true,
          reason: "incident-42",
          disabled_by: "op-1",
          disabled_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
        },
      }),
    );
    renderPage();

    expect(await screen.findByTestId("agent-disabled-tag")).toBeInTheDocument();
    expect(screen.getByTestId("agent-enable-btn")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-disable-btn")).not.toBeInTheDocument();
  });

  it("disable confirms with a reason and calls disableAgent", async () => {
    const user = userEvent.setup();
    vi.mocked(getAgent).mockResolvedValue(detail());
    vi.mocked(disableAgent).mockResolvedValue({
      name: "code-reviewer",
      disabled: true,
      cancelled_runs: 2,
    });
    renderPage();

    await user.click(await screen.findByTestId("agent-disable-btn"));
    await user.type(
      await screen.findByLabelText("Reason (optional, shown in audit)"),
      "incident-9",
    );
    // Popconfirm OK shares the "Disable" label with the trigger; the confirm
    // button is the last one rendered (in the popover portal).
    const buttons = await screen.findAllByRole("button", { name: "Disable" });
    await user.click(buttons[buttons.length - 1]);

    expect(disableAgent).toHaveBeenCalledWith("code-reviewer", "incident-9");
  });

  it("disable with a blank reason passes undefined (not an empty string)", async () => {
    const user = userEvent.setup();
    vi.mocked(getAgent).mockResolvedValue(detail());
    vi.mocked(disableAgent).mockResolvedValue({
      name: "code-reviewer",
      disabled: true,
      cancelled_runs: 0,
    });
    renderPage();

    await user.click(await screen.findByTestId("agent-disable-btn"));
    // Leave the reason box empty (and whitespace is trimmed away).
    const buttons = await screen.findAllByRole("button", { name: "Disable" });
    await user.click(buttons[buttons.length - 1]);

    expect(disableAgent).toHaveBeenCalledWith("code-reviewer", undefined);
  });

  it("enable calls enableAgent", async () => {
    const user = userEvent.setup();
    vi.mocked(getAgent).mockResolvedValue(
      detail({
        disabled: true,
        disable: {
          tenant_id: "22222222-2222-2222-2222-222222222222",
          agent_name: "code-reviewer",
          disabled: true,
          reason: null,
          disabled_by: "op-1",
          disabled_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
        },
      }),
    );
    vi.mocked(enableAgent).mockResolvedValue({ name: "code-reviewer", disabled: false });
    renderPage();

    await user.click(await screen.findByTestId("agent-enable-btn"));
    // Popconfirm OK is labelled "Enable" (shared with the trigger button).
    const buttons = await screen.findAllByRole("button", { name: "Enable" });
    await user.click(buttons[buttons.length - 1]);

    expect(enableAgent).toHaveBeenCalledWith("code-reviewer");
  });
});
