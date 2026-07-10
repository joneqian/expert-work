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
  durationMs: null,
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
  durationMs: null,
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
  durationMs: null,
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

const toolWithDuration: ToolCallEntry = { ...tool, id: "c2", durationMs: 840 };

const agentStepWithDuration: AgentStep = {
  ...agentStep,
  seq: 10,
  durationMs: 1200,
  tools: [toolWithDuration],
};

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

  it("renders fmtDuration for an agent step's duration and its nested tool's duration, each with the tl_duration aria-label", () => {
    render(<StepTimeline items={[agentStepWithDuration]} />);
    fireEvent.click(screen.getByTestId("step-head"));
    const durations = screen.getAllByLabelText("step duration");
    expect(durations).toHaveLength(2);
    expect(screen.getByText("1.2s")).toBeInTheDocument();
    expect(screen.getByText("840ms")).toBeInTheDocument();
  });

  it("hides the duration element for a step (and its nested tool) with durationMs: null", () => {
    render(<StepTimeline items={[agentStep]} />);
    fireEvent.click(screen.getByTestId("step-head"));
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
    expect(screen.queryByLabelText("step duration")).not.toBeInTheDocument();
  });

  it("renders fmtDuration for an aux node row's duration at the end of its summary line", () => {
    const memRowWithDuration: AuxNodeItem = { ...memRow, seq: 11, durationMs: 3200 };
    render(<StepTimeline items={[memRowWithDuration]} />);
    expect(screen.getByLabelText("step duration")).toBeInTheDocument();
    expect(screen.getByText("3.2s")).toBeInTheDocument();
  });
});
