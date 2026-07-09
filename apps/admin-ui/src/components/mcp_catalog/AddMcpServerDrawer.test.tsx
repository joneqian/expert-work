import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import "../../i18n";

import { AddMcpServerDrawer } from "./AddMcpServerDrawer";
import * as catalogSdk from "../../api/mcp-catalog";

const listMock = vi.spyOn(catalogSdk, "listTenantCatalog");
const enableMock = vi.spyOn(catalogSdk, "enablePlatformServer");

beforeEach(() => {
  listMock.mockReset();
  enableMock.mockReset();
});

function renderDrawer(onEnabledChange: () => void) {
  return render(
    <App>
      <AddMcpServerDrawer
        open
        onClose={() => {}}
        onSaved={() => {}}
        onEnabledChange={onEnabledChange}
      />
    </App>,
  );
}

describe("AddMcpServerDrawer", () => {
  it("fires onEnabledChange after a successful enable toggle", async () => {
    listMock.mockResolvedValue([
      {
        id: "c1",
        name: "amap-maps",
        display_name: "高德地图",
        description: "",
        transport: "streamable_http",
        auth_type: "bearer",
        category: "location",
        required_tier: "free",
        entitled: true,
        tenant_enabled: false,
      },
    ] as never);
    enableMock.mockResolvedValue({} as never);
    const onEnabledChange = vi.fn();
    renderDrawer(onEnabledChange);

    const toggle = await screen.findByTestId("cb-toggle-amap-maps");
    await userEvent.click(toggle);

    await waitFor(() => expect(enableMock).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(onEnabledChange).toHaveBeenCalledTimes(1));
  });
});
