import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
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
});
