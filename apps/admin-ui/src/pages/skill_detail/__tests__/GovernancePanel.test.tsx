/**
 * GovernancePanel tests — Stream SE (SE-8-4).
 *
 * The skill-evolution SDK is mocked (it would otherwise hit fetch); we drive
 * the pending-request state from the test body and assert the propose /
 * approve / reject affordances + visibility badge.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../../i18n";

import * as sdk from "../../../api/skill-evolution";
import type { PromoteRequest } from "../../../api/skill-evolution";
import type { SkillRecord } from "../../../api/skills";
import { GovernancePanel } from "../GovernancePanel";

const listMock = vi.spyOn(sdk, "listPromoteRequests");
const requestMock = vi.spyOn(sdk, "requestPromote");
const approveMock = vi.spyOn(sdk, "approvePromote");

function skill(overrides: Partial<SkillRecord> = {}): SkillRecord {
  return {
    id: "sk-1",
    name: "researcher",
    status: "draft",
    latest_version: 2,
    description: "",
    category: "research",
    pinned: false,
    last_used_at: null,
    state_changed_at: null,
    created_at: "2026-06-08T00:00:00Z",
    updated_at: "2026-06-08T00:00:00Z",
    visibility: "agent_private",
    created_by_agent_name: "assistant",
    ...overrides,
  };
}

function pending(): PromoteRequest {
  return {
    id: "req-1",
    tenant_id: "t1",
    skill_id: "sk-1",
    skill_version: 2,
    status: "pending",
    requested_by_user_id: null,
    requested_by_agent_name: "assistant",
    reason: "",
    decided_by_user_id: null,
    decided_at: null,
    decision_reason: "",
    created_at: "2026-06-08T00:00:00Z",
  };
}

function renderPanel(props: Parameters<typeof GovernancePanel>[0]) {
  return render(
    <App>
      <GovernancePanel {...props} />
    </App>,
  );
}

beforeEach(() => {
  listMock.mockReset();
  requestMock.mockReset();
  approveMock.mockReset();
  if (typeof window !== "undefined") window.localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("GovernancePanel", () => {
  it("shows the propose button for an agent_private skill with no pending request", async () => {
    listMock.mockResolvedValue({ items: [], next_cursor: null, cross_tenant: false });
    renderPanel({ skill: skill(), isAdmin: false, onChanged: vi.fn() });

    expect(screen.getByTestId("skill-visibility-badge")).toHaveTextContent(/agent/i);
    await waitFor(() =>
      expect(screen.getByTestId("skill-propose-button")).toBeInTheDocument(),
    );
  });

  it("proposing calls requestPromote with the latest version", async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ items: [], next_cursor: null, cross_tenant: false });
    requestMock.mockResolvedValue(pending());
    renderPanel({ skill: skill(), isAdmin: false, onChanged: vi.fn() });

    await waitFor(() => screen.getByTestId("skill-propose-button"));
    await user.click(screen.getByTestId("skill-propose-button"));
    expect(requestMock).toHaveBeenCalledWith("sk-1", { skill_version: 2 });
  });

  it("renders approve/reject for an admin when a request is pending", async () => {
    listMock.mockResolvedValue({ items: [pending()], next_cursor: null, cross_tenant: false });
    renderPanel({ skill: skill(), isAdmin: true, onChanged: vi.fn() });

    await waitFor(() => expect(screen.getByTestId("skill-pending-promotion")).toBeInTheDocument());
    expect(screen.getByTestId("skill-approve-button")).toBeInTheDocument();
    expect(screen.getByTestId("skill-reject-button")).toBeInTheDocument();
    expect(screen.queryByTestId("skill-propose-button")).not.toBeInTheDocument();
  });

  it("hides approve/reject for a non-admin even when pending", async () => {
    listMock.mockResolvedValue({ items: [pending()], next_cursor: null, cross_tenant: false });
    renderPanel({ skill: skill(), isAdmin: false, onChanged: vi.fn() });

    await waitFor(() => expect(screen.getByTestId("skill-pending-promotion")).toBeInTheDocument());
    expect(screen.queryByTestId("skill-approve-button")).not.toBeInTheDocument();
  });

  it("shows the tenant badge for a tenant-visible skill", async () => {
    listMock.mockResolvedValue({ items: [], next_cursor: null, cross_tenant: false });
    renderPanel({ skill: skill({ visibility: "tenant" }), isAdmin: true, onChanged: vi.fn() });
    expect(screen.getByTestId("skill-visibility-badge")).toHaveTextContent(/tenant/i);
    expect(screen.queryByTestId("skill-propose-button")).not.toBeInTheDocument();
  });
});
