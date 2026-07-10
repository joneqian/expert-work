/**
 * AgentStatePanels — renders the parsed AgentState channels (recalled
 * memories, tool failures, reflections, subagent calls, run signals) plus
 * retry entries and per-step token usage for a playground turn. Returns
 * null when everything is empty.
 */
import type { ReactNode } from "react";
import { Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { AgentStateView } from "../../../api/agent_state";
import type { RetryEntry } from "../../../api/tool_timeline";
import type { StepUsage } from "../../../api/turn_summary";

const { Text } = Typography;

export interface AgentStatePanelsProps {
  state: AgentStateView;
  retries: RetryEntry[];
  perStepUsage: StepUsage[];
}

export function AgentStatePanels({ state, retries, perStepUsage }: AgentStatePanelsProps) {
  const { t } = useTranslation();
  const { recalledMemories, toolFailures, reflections, subagentInvocations, signals } = state;
  const hasSignals =
    (signals.noProgressStreak ?? 0) > 0 || signals.escalateNext === true;
  const anything =
    recalledMemories.length || toolFailures.length || reflections.length ||
    subagentInvocations.length || retries.length || perStepUsage.length || hasSignals;
  if (!anything) return null;

  const section = (testId: string, label: string, body: ReactNode) => (
    <div data-testid={testId} style={{ marginTop: 6 }}>
      <Text type="secondary" style={{ fontSize: 11 }}>{label}</Text>
      <div style={{ marginTop: 2 }}>{body}</div>
    </div>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {recalledMemories.length > 0 &&
        section("agent-state-memories", t("playground.state_memories"),
          recalledMemories.map((m) => (
            <div key={m.id} style={{ fontSize: 12 }}>
              <Tag bordered={false}>{m.kind}</Tag>{m.content}
            </div>
          )))}
      {toolFailures.length > 0 &&
        section("agent-state-failures", t("playground.state_failures"),
          toolFailures.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--ew-text-danger, #cf1322)" }}>
              <span className="mono">{f.toolName}</span> · {f.errorClass} — {f.advice}
            </div>
          )))}
      {reflections.length > 0 &&
        section("agent-state-reflections", t("playground.state_reflections"),
          reflections.map((r, i) => (
            <div key={i} style={{ fontSize: 12 }}>
              <Tag bordered={false} color={r.verdict === "revise" ? "orange" : "green"}>{r.verdict}</Tag>
              {r.critique}
            </div>
          )))}
      {subagentInvocations.length > 0 &&
        section("agent-state-subagents", t("playground.state_subagents"),
          subagentInvocations.map((s) => (
            <div key={s.taskId} style={{ fontSize: 12 }}>
              <span className="mono">{s.name}</span> · {s.status} ·{" "}
              {s.iterationUsed}it/{s.llmCallCount}call/{s.wallClockMs}ms
            </div>
          )))}
      {retries.length > 0 &&
        section("agent-state-retries", t("playground.state_retries"),
          retries.map((r, i) => (
            <Tag key={i} bordered={false} color="orange" style={{ margin: "0 4px 4px 0" }}>
              {t("playground.retry_attempt", { n: r.attempt, cls: r.errorClass, s: r.backoffS })}
            </Tag>
          )))}
      {perStepUsage.length > 0 &&
        section("agent-state-per-step", t("playground.state_per_step"),
          perStepUsage.map((u, i) => (
            <div key={i} style={{ fontSize: 12 }} className="mono">
              {u.node} #{u.stepCount ?? "?"} · {u.usage.totalTokens} tok
            </div>
          )))}
      {hasSignals &&
        section("agent-state-signals", t("playground.state_signals"),
          <span style={{ fontSize: 12 }}>
            {(signals.noProgressStreak ?? 0) > 0 && (
              <Tag color="volcano" bordered={false}>
                {t("playground.signal_no_progress")}: {signals.noProgressStreak}
              </Tag>
            )}
            {signals.escalateNext === true && (
              <Tag color="red" bordered={false}>{t("playground.signal_escalate")}</Tag>
            )}
          </span>)}
    </div>
  );
}
