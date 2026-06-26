import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../../i18n";

import { McpToolPicker } from "../McpToolPicker";
import * as serversSdk from "../../../../api/mcp-servers";
import * as catalogSdk from "../../../../api/mcp-catalog";

const availableMock = vi.spyOn(serversSdk, "listAvailableMcpServers");
const catalogMock = vi.spyOn(catalogSdk, "listPlatformCatalog");
const toolsMock = vi.spyOn(serversSdk, "listMcpServerTools");

beforeEach(() => {
  availableMock.mockReset();
  catalogMock.mockReset();
  toolsMock.mockReset();
});

const noop = () => {};

describe("McpToolPicker", () => {
  it("default (available) lists opted-in servers from /available", async () => {
    availableMock.mockResolvedValue([
      { name: "amap-maps", source: "platform" },
      { name: "my-custom", source: "tenant" },
    ]);
    render(<McpToolPicker servers={[]} allowTools={[]} onChange={noop} />);
    expect(
      await screen.findByTestId("af-mcp-server-amap-maps"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("af-mcp-server-my-custom")).toBeInTheDocument();
    expect(catalogMock).not.toHaveBeenCalled();
  });

  it("catalog source lists published connectors (enabled only) by display name", async () => {
    catalogMock.mockResolvedValue([
      {
        id: "c1",
        name: "amap-maps",
        display_name: "高德地图",
        transport: "streamable-http",
        auth_type: "none",
        category: "location",
        required_tier: "free",
        enabled: true,
      },
      {
        id: "c2",
        name: "draft-conn",
        display_name: "Draft",
        transport: "streamable-http",
        auth_type: "none",
        category: "other",
        required_tier: "free",
        enabled: false,
      },
    ] as never);
    render(
      <McpToolPicker
        source="catalog"
        servers={[]}
        allowTools={[]}
        onChange={noop}
      />,
    );
    await screen.findByTestId("af-mcp-server-amap-maps");
    expect(screen.getByText("高德地图")).toBeInTheDocument();
    expect(
      screen.queryByTestId("af-mcp-server-draft-conn"),
    ).not.toBeInTheDocument();
    expect(availableMock).not.toHaveBeenCalled();
  });

  it("catalog empty state points to the MCP Catalog page", async () => {
    catalogMock.mockResolvedValue([]);
    render(
      <McpToolPicker
        source="catalog"
        servers={[]}
        allowTools={[]}
        onChange={noop}
      />,
    );
    const empty = await screen.findByTestId("af-mcp-empty");
    expect(empty.textContent).toMatch(/MCP 目录|MCP Catalog/);
  });

  it("available empty state points to the MCP Servers page", async () => {
    availableMock.mockResolvedValue([]);
    render(<McpToolPicker servers={[]} allowTools={[]} onChange={noop} />);
    const empty = await screen.findByTestId("af-mcp-empty");
    expect(empty.textContent).toMatch(/MCP 服务器|MCP Servers/);
  });

  it("checking a server enables MCP — emits the server, no separate toggle", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    availableMock.mockResolvedValue([
      { name: "amap-maps", source: "platform" },
    ]);
    render(<McpToolPicker servers={[]} allowTools={[]} onChange={onChange} />);
    await user.click(await screen.findByTestId("af-mcp-server-amap-maps"));
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(["amap-maps"], []),
    );
    // There is no separate MCP enable checkbox.
    expect(screen.queryByTestId("af-tool-mcp")).not.toBeInTheDocument();
  });

  it("a checked server defaults to 'all tools'; switching to 'specific' reveals the tool list", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    availableMock.mockResolvedValue([
      { name: "amap-maps", source: "platform" },
    ]);
    toolsMock.mockResolvedValue([
      { name: "maps_geo", description: "" },
      { name: "maps_weather", description: "" },
    ]);
    render(
      <McpToolPicker
        servers={["amap-maps"]}
        allowTools={[]}
        onChange={onChange}
      />,
    );
    // Scope control present; tool list hidden under "all".
    const scope = await screen.findByTestId("af-mcp-scope-amap-maps");
    expect(scope).toBeInTheDocument();
    expect(
      screen.queryByTestId("af-mcp-tools-amap-maps"),
    ).not.toBeInTheDocument();
    // Switch to "specific" → tools load + show. (i18n may be en or zh.)
    await user.click(screen.getByText(/指定工具|Specific/));
    expect(
      await screen.findByTestId("af-mcp-tool-maps_geo"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(`af-mcp-select-all-amap-maps`),
    ).toBeInTheDocument();
  });

  it("select-all adds every tool of the server to allow_tools", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    availableMock.mockResolvedValue([
      { name: "amap-maps", source: "platform" },
    ]);
    toolsMock.mockResolvedValue([
      { name: "maps_geo", description: "" },
      { name: "maps_weather", description: "" },
    ]);
    // Seed one allow_tool so the scope derives to "specific" (tool list shown).
    render(
      <McpToolPicker
        servers={["amap-maps"]}
        allowTools={["maps_geo"]}
        onChange={onChange}
      />,
    );
    await user.click(await screen.findByTestId("af-mcp-select-all-amap-maps"));
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        ["amap-maps"],
        expect.arrayContaining(["maps_geo", "maps_weather"]),
      ),
    );
  });

  it("filters tools by the per-server search box", async () => {
    const user = userEvent.setup();
    availableMock.mockResolvedValue([
      { name: "amap-maps", source: "platform" },
    ]);
    toolsMock.mockResolvedValue([
      { name: "maps_geo", description: "" },
      { name: "maps_weather", description: "" },
    ]);
    render(
      <McpToolPicker
        servers={["amap-maps"]}
        allowTools={["maps_geo"]}
        onChange={noop}
      />,
    );
    await screen.findByTestId("af-mcp-tool-maps_geo");
    await user.type(
      screen.getByTestId("af-mcp-tool-search-amap-maps"),
      "weather",
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("af-mcp-tool-maps_geo"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("af-mcp-tool-maps_weather")).toBeInTheDocument();
  });
});
