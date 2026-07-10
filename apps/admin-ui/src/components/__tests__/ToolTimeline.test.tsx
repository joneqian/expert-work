import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../i18n";

import { ToolTimeline } from "../ToolTimeline";
import type { SseEvent } from "../../api/sessions";

function updates(node: string, messages: unknown[]): SseEvent {
  return { id: null, event: "updates", data: { [node]: { messages } }, rawData: "", receivedAt: "" };
}

describe("ToolTimeline", () => {
  it("shows an empty state when there are no tool calls", () => {
    render(<ToolTimeline events={[]} />);
    expect(screen.getByTestId("tool-timeline-empty")).toBeInTheDocument();
  });

  it("renders an MCP call with its server + tool name and a success status", () => {
    const events = [
      updates("agent", [
        {
          type: "ai",
          content: "",
          tool_calls: [
            {
              id: "c1",
              name: "mcp:amap-maps.maps_direction_driving",
              args: { origin: "a" },
              type: "tool_call",
            },
          ],
        },
      ]),
      updates("tools", [
        { type: "tool", tool_call_id: "c1", name: null, content: "{\"d\":1}", status: "success" },
      ]),
    ];
    render(<ToolTimeline events={events} />);
    expect(screen.getByTestId("tool-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
    // MCP badge carries the server name.
    expect(screen.getByText(/amap-maps/)).toBeInTheDocument();
    expect(screen.getByText("maps_direction_driving")).toBeInTheDocument();
  });

  it("renders a structured exec_python result with an exit-code chip", () => {
    const events = [
      updates("agent", [
        {
          type: "ai",
          content: "",
          tool_calls: [
            { id: "c1", name: "exec_python", args: { code: "print(1)" }, type: "tool_call" },
          ],
        },
      ]),
      updates("tools", [
        { type: "tool", tool_call_id: "c1", name: null, content: "stdout:\n1\n\nexit_code: 0", status: "success" },
      ]),
    ];
    render(<ToolTimeline events={events} />);
    // The result panel is collapsed by default — open it to reveal the
    // structured exec-result content.
    fireEvent.click(screen.getByText(/^(Result|结果)$/));
    expect(screen.getByTestId("tool-exec-result")).toBeInTheDocument();
    expect(screen.getByTestId("tool-exit-code")).toHaveTextContent("0");
  });
});
