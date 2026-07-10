/**
 * AgentState channel parser — distills the debug-relevant AgentState channels
 * out of a turn's SSE ``updates`` frames (``{node: {channel: value}}``).
 *
 * Requires the backend ``_to_jsonable`` fix (pydantic/dataclass → JSON); before
 * it these channels arrive as Python repr strings and cannot be parsed.
 */
import type { SseEvent } from "./sessions";

export interface RecalledMemory {
  id: string;
  kind: string;
  content: string;
  importance: number;
  confidence: number;
}
export interface ToolFailure {
  toolName: string;
  errorClass: string;
  summary: string;
  retryable: boolean;
  advice: string;
}
export interface AgentReflection {
  verdict: string;
  critique: string;
}
export interface SubagentInvocation {
  taskId: string;
  name: string;
  agentRef: string;
  status: string;
  iterationUsed: number;
  llmCallCount: number;
  wallClockMs: number;
  resultExcerpt: string;
  error: string | null;
}
export interface AgentSignals {
  noProgressStreak: number | null;
  escalateNext: boolean | null;
  stepCountRefundPending: number | null;
}
export interface AgentStateView {
  recalledMemories: RecalledMemory[];
  toolFailures: ToolFailure[];
  reflections: AgentReflection[];
  subagentInvocations: SubagentInvocation[];
  signals: AgentSignals;
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function asObjArray(v: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is Record<string, unknown> => x !== null && typeof x === "object");
}

export function parseAgentState(events: readonly SseEvent[]): AgentStateView {
  let recalledMemories: RecalledMemory[] = [];
  const toolFailures: ToolFailure[] = [];
  const reflections: AgentReflection[] = [];
  const subagentByTask = new Map<string, SubagentInvocation>();
  const subagentOrder: string[] = [];
  const signals: AgentSignals = {
    noProgressStreak: null,
    escalateNext: null,
    stepCountRefundPending: null,
  };

  for (const evt of events) {
    if (evt.event !== "updates" || evt.data === null || typeof evt.data !== "object") continue;
    for (const node of Object.values(evt.data as Record<string, unknown>)) {
      if (node === null || typeof node !== "object") continue;
      const ch = node as Record<string, unknown>;

      if ("recalled_memories" in ch) {
        const mems = asObjArray(ch.recalled_memories).map((m) => ({
          id: str(m.id),
          kind: str(m.kind),
          content: str(m.content),
          importance: num(m.importance),
          confidence: num(m.confidence),
        }));
        if (mems.length > 0) recalledMemories = mems; // last non-empty wins
      }

      if ("tool_failures" in ch) {
        for (const f of asObjArray(ch.tool_failures)) {
          toolFailures.push({
            toolName: str(f.tool_name),
            errorClass: str(f.error_class),
            summary: str(f.summary),
            retryable: f.retryable === true,
            advice: str(f.advice),
          });
        }
      }

      if ("reflections" in ch) {
        for (const r of asObjArray(ch.reflections)) {
          reflections.push({ verdict: str(r.verdict), critique: str(r.critique) });
        }
      }

      if ("subagent_invocations" in ch) {
        for (const s of asObjArray(ch.subagent_invocations)) {
          const taskId = str(s.task_id);
          if (taskId === "") continue;
          if (!subagentByTask.has(taskId)) subagentOrder.push(taskId);
          subagentByTask.set(taskId, {
            taskId,
            name: str(s.name),
            agentRef: str(s.agent_ref),
            status: str(s.status),
            iterationUsed: num(s.iteration_used),
            llmCallCount: num(s.llm_call_count),
            wallClockMs: num(s.wall_clock_ms),
            resultExcerpt: str(s.result_excerpt),
            error: typeof s.error === "string" ? s.error : null,
          });
        }
      }

      if (typeof ch.no_progress_streak === "number") signals.noProgressStreak = ch.no_progress_streak;
      if (typeof ch.escalate_next === "boolean") signals.escalateNext = ch.escalate_next;
      if (typeof ch.step_count_refund_pending === "number") {
        signals.stepCountRefundPending = ch.step_count_refund_pending;
      }
    }
  }

  return {
    recalledMemories,
    toolFailures,
    reflections,
    subagentInvocations: subagentOrder.map((id) => subagentByTask.get(id) as SubagentInvocation),
    signals,
  };
}
