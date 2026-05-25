/**
 * Stream H CI smoke test — proves the React + Antd + tokens stack
 * compiles, types check, jsdom mounts, and a simple Antd component
 * renders. Detailed component tests live next to each page in H.2+.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ConfigProvider, Button } from "antd";

describe("admin-ui smoke", () => {
  it("renders an Antd Button inside ConfigProvider", () => {
    render(
      <ConfigProvider>
        <Button data-testid="probe">hello</Button>
      </ConfigProvider>,
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("hello");
  });
});
