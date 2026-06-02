/**
 * Members page tests — Stream U PR F: set temporary password action.
 *
 * An admin opens the "Set password" modal for an active member, types a
 * temporary password, and submits. The member must change it on first
 * login (backend forces ``temporary=true``). Short passwords surface an
 * inline error and never hit the API.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsMembers } from "../SettingsMembers";
import { AuthProvider } from "../../auth/AuthContext";
import { TenantScopeProvider } from "../../tenant/TenantScopeContext";
import { setStoredToken } from "../../api/client";
import {
  listMembers,
  resetMemberPassword,
  type TenantMember,
} from "../../api/members";

vi.mock("../../api/members", () => ({
  listMembers: vi.fn(),
  inviteMembers: vi.fn(),
  resendMember: vi.fn(),
  revokeMember: vi.fn(),
  resetMemberPassword: vi.fn(),
}));

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

const activeMember: TenantMember = {
  id: "m-1",
  tenant_id: "t1",
  email: "alice@example.com",
  display_name: "Alice",
  role: "admin",
  status: "active",
  keycloak_user_id: "kc-1",
  subject_id: "s-1",
  invited_by: "u1",
  invited_at: "2026-05-26T10:00:00Z",
  activated_at: "2026-05-27T10:00:00Z",
  updated_at: "2026-05-27T10:00:00Z",
};

function renderPage(): void {
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin"] }));
  render(
    <MemoryRouter>
      <AuthProvider>
        <TenantScopeProvider>
          <App>
            <SettingsMembers />
          </App>
        </TenantScopeProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  setStoredToken(null);
  window.sessionStorage.clear();
  vi.clearAllMocks();
});

describe("SettingsMembers — set password", () => {
  it("opens the modal and sets a valid temporary password", async () => {
    vi.mocked(listMembers).mockResolvedValue({ items: [activeMember], total: 1 });
    vi.mocked(resetMemberPassword).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("members-set-password-m-1")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("members-set-password-m-1"));

    await waitFor(() =>
      expect(screen.getByTestId("members-set-password-input")).toBeInTheDocument(),
    );
    await user.type(
      screen.getByTestId("members-set-password-input"),
      "s3cret-pass",
    );
    await user.click(screen.getByTestId("members-set-password-submit"));

    await waitFor(() =>
      expect(resetMemberPassword).toHaveBeenCalledWith("m-1", "s3cret-pass"),
    );
  });

  it("rejects a too-short password without calling the API", async () => {
    vi.mocked(listMembers).mockResolvedValue({ items: [activeMember], total: 1 });
    const user = userEvent.setup();
    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("members-set-password-m-1")).toBeInTheDocument(),
    );
    await user.click(screen.getByTestId("members-set-password-m-1"));

    await waitFor(() =>
      expect(screen.getByTestId("members-set-password-input")).toBeInTheDocument(),
    );
    await user.type(screen.getByTestId("members-set-password-input"), "short");
    await user.click(screen.getByTestId("members-set-password-submit"));

    expect(screen.getByTestId("members-set-password-error")).toBeInTheDocument();
    expect(resetMemberPassword).not.toHaveBeenCalled();
  });
});
