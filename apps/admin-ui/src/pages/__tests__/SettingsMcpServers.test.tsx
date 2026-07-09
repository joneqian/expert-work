import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "antd";
import "../../i18n";
import i18n from "../../i18n";

import { SettingsMcpServers } from "../SettingsMcpServers";
import * as serversSdk from "../../api/mcp-servers";
import type { McpServer } from "../../api/mcp-servers";

const listMock = vi.spyOn(serversSdk, "listMcpServers");
const availMock = vi.spyOn(serversSdk, "listAvailableMcpServers");

// The default test-time locale resolves to "en" (jsdom's navigator.language
// is "en-US"; see AgentsList.test.tsx for the same forced-locale idiom) —
// force zh-CN so the source-tag assertions below ("平台"/"自定义") are
// deterministic regardless of the jsdom-detected browser language.
beforeEach(async () => {
  await i18n.changeLanguage("zh-CN");
  listMock.mockReset();
  availMock.mockReset();
});

const custom: McpServer = {
  id: "s1",
  name: "my-custom",
  transport: "sse",
  url: "https://x.example.com/sse",
  auth_type: "bearer",
  timeout_s: 30,
  enabled: true,
  created_at: "",
  updated_at: "",
};

function renderPage() {
  return render(
    <MemoryRouter>
      <App>
        <SettingsMcpServers />
      </App>
    </MemoryRouter>,
  );
}

describe("SettingsMcpServers unified list", () => {
  it("renders platform and custom rows with source tags", async () => {
    listMock.mockResolvedValue([custom]);
    availMock.mockResolvedValue([
      { name: "amap", source: "platform", display_name: "高德地图", auth_type: "none", catalog_id: "c1" },
    ]);
    renderPage();
    expect(await screen.findByText("高德地图")).toBeInTheDocument();
    expect(screen.getByText("my-custom")).toBeInTheDocument();
    expect(screen.getAllByText("平台").length).toBeGreaterThan(0);
    expect(screen.getAllByText("自定义").length).toBeGreaterThan(0);
  });

  it("oauth2 platform row shows the authorize link and hides Test", async () => {
    listMock.mockResolvedValue([]);
    availMock.mockResolvedValue([
      { name: "gh", source: "platform", display_name: "GitHub", auth_type: "oauth2", catalog_id: "c2" },
    ]);
    renderPage();
    expect(await screen.findByTestId("ms-authorize-gh")).toBeInTheDocument();
    expect(screen.queryByTestId("ms-test-gh")).not.toBeInTheDocument();
  });
});
