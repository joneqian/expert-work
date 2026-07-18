import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import en from "../../../i18n/locales/en";
import { CONFIG_GROUPS } from "../groups";
import { GroupNav } from "../GroupNav";

describe("GroupNav", () => {
  it("renders one nav node per registered group", () => {
    render(<GroupNav active="basic" onSelect={vi.fn()} />);
    for (const group of CONFIG_GROUPS) {
      expect(screen.getByTestId(`cfg-nav-${group.id}`)).toBeInTheDocument();
    }
    expect(screen.getAllByRole("menuitem")).toHaveLength(CONFIG_GROUPS.length);
  });

  it("labels each node with its i18n group label", () => {
    render(<GroupNav active="basic" onSelect={vi.fn()} />);
    expect(screen.getByTestId("cfg-nav-basic")).toHaveTextContent(
      en.manifest_editor.group_basic,
    );
    expect(screen.getByTestId("cfg-nav-security")).toHaveTextContent(
      en.manifest_editor.group_security,
    );
  });

  it("highlights only the active group", () => {
    render(<GroupNav active="model" onSelect={vi.fn()} />);
    expect(screen.getByTestId("cfg-nav-model")).toHaveClass(
      "ant-menu-item-selected",
    );
    expect(screen.getByTestId("cfg-nav-basic")).not.toHaveClass(
      "ant-menu-item-selected",
    );
  });

  it("calls onSelect with the group id when a node is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<GroupNav active="basic" onSelect={onSelect} />);

    await user.click(screen.getByTestId("cfg-nav-memory"));

    expect(onSelect).toHaveBeenCalledWith("memory");
  });

  it("prepends a leading node above the registered groups", () => {
    render(
      <GroupNav
        active="tpl-meta"
        onSelect={vi.fn()}
        leading={{ value: "tpl-meta", label: "Template info" }}
      />,
    );

    const leadingNode = screen.getByTestId("cfg-nav-tpl-meta");
    expect(leadingNode).toHaveTextContent("Template info");
    expect(leadingNode).toHaveClass("ant-menu-item-selected");

    const nodes = screen.getAllByRole("menuitem");
    expect(nodes[0]).toBe(leadingNode);
    expect(nodes).toHaveLength(CONFIG_GROUPS.length + 1);
  });

  it("calls onSelect with the leading value when the leading node is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <GroupNav
        active="basic"
        onSelect={onSelect}
        leading={{ value: "tpl-meta", label: "Template info" }}
      />,
    );

    await user.click(screen.getByTestId("cfg-nav-tpl-meta"));

    expect(onSelect).toHaveBeenCalledWith("tpl-meta");
  });

  it("omits the leading node when none is supplied", () => {
    render(<GroupNav active="basic" onSelect={vi.fn()} />);
    expect(screen.queryByTestId("cfg-nav-tpl-meta")).not.toBeInTheDocument();
  });
});
