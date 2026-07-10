import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "../../../../i18n";

import { StepTimeline } from "../StepTimeline";
import type { AgentStep, AuxNodeItem, MarkerItem, TimelineItem } from "../../../../api/timeline";
import type { ToolCallEntry } from "../../../../api/tool_timeline";

const tool: ToolCallEntry = {
  id: "c1",
  rawName: "exec_python",
  isMcp: false,
  server: null,
  toolName: "exec_python",
  args: { code: "print(1)" },
  status: "success",
  resultPreview: "1",
};

const agentStep: AgentStep = {
  kind: "agent",
  seq: 0,
  receivedAt: "t1",
  stepCount: 1,
  node: "agent",
  model: "glm-5.2",
  finishReason: "tool_calls",
  reasoning: "先查天气",
  content: null,
  inputTokens: 100,
  outputTokens: 10,
  totalTokens: 110,
  tools: [tool],
  hasError: false,
};

const memRow: AuxNodeItem = {
  kind: "memory_recall",
  seq: 1,
  receivedAt: "t2",
  node: "memory_recall",
  summary: "记忆召回 · 2 条",
  detail: {
    memories: [{ id: "m1", kind: "fact", content: "住嘉兴", importance: 0.7, confidence: 0.9 }],
  },
  tone: "normal",
};

const retryMarker: MarkerItem = {
  kind: "retry",
  seq: 2,
  receivedAt: "t3",
  text: "重试 #1 · TimeoutError · 退避 2.0s",
  tone: "warn",
};

function items(): TimelineItem[] {
  return [agentStep, memRow, retryMarker];
}

describe("StepTimeline", () => {
  it("returns null (empty container) when items is empty", () => {
    const { container } = render(<StepTimeline items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the axis container", () => {
    render(<StepTimeline items={items()} />);
    expect(screen.getByTestId("step-timeline")).toBeInTheDocument();
  });

  it("shows the agent step's model + finish_reason, and its nested tool card once expanded", () => {
    render(<StepTimeline items={items()} />);
    expect(screen.getByText(/glm-5\.2/)).toBeInTheDocument();
    expect(screen.getByText(/tool_calls/)).toBeInTheDocument();
    // Normal step defaults collapsed — expand it to reveal the nested tool card.
    fireEvent.click(screen.getByTestId("step-head"));
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
  });

  it("shows the memory_recall aux row's type label and count summary", () => {
    render(<StepTimeline items={items()} />);
    expect(screen.getByText("memory_recall")).toBeInTheDocument();
    expect(screen.getByText(/记忆召回 · 2 条/)).toBeInTheDocument();
  });

  it("shows the retry marker's text", () => {
    render(<StepTimeline items={items()} />);
    expect(screen.getByText(/重试 #1 · TimeoutError/)).toBeInTheDocument();
  });

  it("defaults an errored agent step to expanded (shows tool card without a click)", () => {
    const errStep: AgentStep = { ...agentStep, seq: 3, hasError: true, finishReason: null };
    render(<StepTimeline items={[errStep]} />);
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
  });
});
