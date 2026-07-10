import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "../../../../i18n";

import { AgentStatePanels } from "../AgentStatePanels";
import type { AgentStateView } from "../../../../api/agent_state";

const empty: AgentStateView = {
  recalledMemories: [], toolFailures: [], reflections: [], subagentInvocations: [],
  signals: { noProgressStreak: null, escalateNext: null, stepCountRefundPending: null },
};

describe("AgentStatePanels", () => {
  it("returns null when everything is empty", () => {
    const { container } = render(<AgentStatePanels state={empty} retries={[]} perStepUsage={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a tool-failure row with its error class", () => {
    render(
      <AgentStatePanels
        state={{ ...empty, toolFailures: [{ toolName: "exec_python", errorClass: "transient", summary: "boom", retryable: true, advice: "retry" }] }}
        retries={[]}
        perStepUsage={[]}
      />,
    );
    expect(screen.getByTestId("agent-state-failures")).toHaveTextContent("exec_python");
    expect(screen.getByTestId("agent-state-failures")).toHaveTextContent("transient");
  });

  it("renders retries and a subagent call", () => {
    render(
      <AgentStatePanels
        state={{ ...empty, subagentInvocations: [{ taskId: "t1", name: "researcher", agentRef: "researcher@1", status: "completed", iterationUsed: 3, llmCallCount: 5, wallClockMs: 1200, resultExcerpt: "done", error: null }] }}
        retries={[{ receivedAt: "t", attempt: 2, errorClass: "TimeoutError", backoffS: 1.5 }]}
        perStepUsage={[]}
      />,
    );
    expect(screen.getByTestId("agent-state-subagents")).toHaveTextContent("researcher");
    expect(screen.getByTestId("agent-state-retries")).toHaveTextContent("TimeoutError");
  });
});
