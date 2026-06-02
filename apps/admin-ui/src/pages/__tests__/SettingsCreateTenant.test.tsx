/**
 * Create-Tenant form tests — tenant_id client-side UUID validation.
 *
 * The backend ``tenant_id`` is a UUID (optional; server auto-generates).
 * A human-typed slug like "leyi-company" used to sail through to the API
 * and bounce back as a raw 422. These tests pin the client-side guard:
 * blank → omitted, valid UUID → forwarded, slug → blocked before the POST.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

import { SettingsCreateTenant } from "../SettingsCreateTenant";
import { AuthProvider } from "../../auth/AuthContext";
import { apiClient, setStoredToken } from "../../api/client";

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.`;
}

interface PostCall {
  body: Record<string, unknown>;
}

let postCalls: PostCall[];

function installAdapter(): void {
  postCalls = [];
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const method = (config.method ?? "get").toLowerCase();
    if (url === "/v1/tenants" && method === "post") {
      const body =
        typeof config.data === "string" ? JSON.parse(config.data) : (config.data ?? {});
      postCalls.push({ body });
      return Promise.resolve({
        data: { success: true, data: { tenant_id: "11111111-1111-1111-1111-111111111111" }, error: null },
        status: 201,
        statusText: "Created",
        headers: {},
        config,
        request: {},
      });
    }
    return Promise.resolve({
      data: {},
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
}

function renderForm(): void {
  // system_admin so the form (not the not-admin alert) renders.
  setStoredToken(makeJwt({ sub: "u1", tenant_id: "t1", roles: ["admin", "system_admin"] }));
  render(
    <MemoryRouter>
      <AuthProvider>
        <App>
          <SettingsCreateTenant />
        </App>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  installAdapter();
});

describe("SettingsCreateTenant tenant_id validation", () => {
  it("blocks a non-UUID tenant_id and does not POST", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByTestId("ct-display-name"), "乐毅大公司");
    await user.type(screen.getByTestId("ct-tenant-id"), "leyi-company");
    await user.click(screen.getByTestId("ct-submit"));

    expect(
      await screen.findByText(/Must be a valid UUID|合法 UUID/),
    ).toBeInTheDocument();
    expect(postCalls).toHaveLength(0);
  });

  it("omits tenant_id when left blank (server auto-generates)", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByTestId("ct-display-name"), "乐毅大公司");
    await user.click(screen.getByTestId("ct-submit"));

    await waitFor(() => expect(postCalls).toHaveLength(1));
    expect(postCalls[0].body.display_name).toBe("乐毅大公司");
    expect(postCalls[0].body).not.toHaveProperty("tenant_id");
  });

  it("forwards a valid UUID tenant_id", async () => {
    const user = userEvent.setup();
    renderForm();
    const uuid = "123e4567-e89b-12d3-a456-426614174000";

    await user.type(screen.getByTestId("ct-display-name"), "乐毅大公司");
    await user.type(screen.getByTestId("ct-tenant-id"), uuid);
    await user.click(screen.getByTestId("ct-submit"));

    await waitFor(() => expect(postCalls).toHaveLength(1));
    expect(postCalls[0].body.tenant_id).toBe(uuid);
  });
});
