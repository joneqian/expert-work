/**
 * Platform MCP server detail page tests — Stream MCP platform-servers.
 *
 * Loads the entry by ``:catalogId`` and renders the 配置 / 工具 tabs. The config
 * tab (active by default) shows the shared form + Save button; the tools tab is
 * covered in SettingsMcpCatalog.test (CatalogToolsTab).
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { App } from "antd";
import { render, screen, waitFor } from "@testing-library/react";
import "../../i18n";

import { McpCatalogDetail } from "../McpCatalogDetail";
import { apiClient } from "../../api/client";

const ENTRY = {
  id: "cat-1",
  name: "amap-maps",
  display_name: "高德地图",
  description: "",
  category: "location",
  icon: "",
  transport: "streamable_http" as const,
  url_template: "https://mcp.amap.com/mcp",
  auth_type: "none" as const,
  disabled_tools: [],
  required_tier: "free" as const,
  enabled: true,
  created_at: "2026-06-25T10:00:00Z",
  updated_at: "2026-06-25T10:00:00Z",
  updated_by: "u1",
};

beforeEach(() => {
  vi.restoreAllMocks();
  apiClient.defaults.adapter = (config) => {
    const url = config.url ?? "";
    const data = url.endsWith("/tools")
      ? {
          success: true,
          data: { status: "ok", tool_count: 0, tools: [], error: null },
          error: null,
        }
      : { success: true, data: ENTRY, error: null };
    return Promise.resolve({
      data,
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      request: {},
    });
  };
});

describe("McpCatalogDetail", () => {
  it("loads the entry and renders the config tab with a Save button", async () => {
    render(
      <App>
        <MemoryRouter initialEntries={["/settings/mcp-catalog/cat-1"]}>
          <Routes>
            <Route
              path="/settings/mcp-catalog/:catalogId"
              element={<McpCatalogDetail />}
            />
          </Routes>
        </MemoryRouter>
      </App>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("cce-form")).toBeInTheDocument(),
    );
    // Page chrome + the edit (PATCH) Save affordance.
    expect(screen.getByTestId("mcd-root")).toBeInTheDocument();
    expect(screen.getByTestId("mcd-save")).toBeInTheDocument();
    // The immutable identifier is prefilled + locked in edit mode.
    expect(screen.getByTestId("cce-name")).toBeDisabled();
  });
});
