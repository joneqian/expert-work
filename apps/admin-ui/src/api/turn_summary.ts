/**
 * Per-turn summary parser — distills a turn's SSE ``updates`` frames into the
 * agent's final answer, its reasoning trace, and token usage.
 *
 * Reads the fields the OpenAI-compat decoder now surfaces (PR #847):
 * ``AIMessage.usage_metadata`` (tokens), ``additional_kwargs.reasoning_content``
 * (thinking trace), and the last AI text content (the answer).
 */
import type { SseEvent } from "./sessions";
import { messagesOf } from "./tool_timeline";

export interface TurnUsage {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  reasoningTokens: number;
}

export interface TurnSummary {
  /** Last AI message text content — the agent's answer (null if none yet). */
  finalText: string | null;
  /** ``reasoning_content`` blocks, in arrival order. */
  reasoning: string[];
  /** Token usage summed across the turn's AI messages (null if none reported). */
  usage: TurnUsage | null;
  /** Highest ``step_count`` seen across the turn's node updates (null if none). */
  stepCount: number | null;
  /** Wall-clock from the turn's first frame to its last, in ms (null if <2 frames). */
  latencyMs: number | null;
  /** ``response_metadata.finish_reason`` of the last AI message that reports one (null if none). */
  finishReason: string | null;
  /** ``response_metadata.model_name`` of the last AI message that reports one (null if none). */
  modelName: string | null;
}

function asInt(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function textOf(content: unknown): string | null {
  if (typeof content === "string") {
    return content.trim() === "" ? null : content;
  }
  // Block-list content (rare on the compat path) — join the text parts.
  if (Array.isArray(content)) {
    const parts = content
      .filter(
        (b): b is { text: string } =>
          b !== null &&
          typeof b === "object" &&
          typeof (b as { text?: unknown }).text === "string",
      )
      .map((b) => b.text);
    const joined = parts.join("");
    return joined.trim() === "" ? null : joined;
  }
  return null;
}

/** Distill a turn's frames into answer + reasoning + usage. */
export function summarizeTurn(events: readonly SseEvent[]): TurnSummary {
  let finalText: string | null = null;
  const reasoning: string[] = [];
  let reported = false;
  let stepCount: number | null = null;
  let finishReason: string | null = null;
  let modelName: string | null = null;
  const usage: TurnUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
    reasoningTokens: 0,
  };

  for (const evt of events) {
    if (evt.event !== "updates") continue;
    // ``step_count`` lives at the node level (alongside ``messages``), not on a
    // message — take the highest seen across the turn.
    if (evt.data !== null && typeof evt.data === "object") {
      for (const node of Object.values(evt.data as Record<string, unknown>)) {
        if (node !== null && typeof node === "object") {
          const sc = (node as Record<string, unknown>).step_count;
          if (typeof sc === "number" && (stepCount === null || sc > stepCount)) {
            stepCount = sc;
          }
        }
      }
    }
    for (const m of messagesOf(evt.data)) {
      if (m.type !== "ai") continue;
      const text = textOf(m.content);
      if (text !== null) finalText = text; // last AI text wins

      const rm = m.response_metadata;
      if (rm !== null && typeof rm === "object") {
        const r = rm as Record<string, unknown>;
        if (typeof r.finish_reason === "string") finishReason = r.finish_reason;
        if (typeof r.model_name === "string") modelName = r.model_name;
      }

      const ak = m.additional_kwargs;
      if (ak !== null && typeof ak === "object") {
        const rc = (ak as Record<string, unknown>).reasoning_content;
        if (typeof rc === "string" && rc.trim() !== "") reasoning.push(rc);
      }

      const um = m.usage_metadata;
      if (um !== null && typeof um === "object") {
        reported = true;
        const u = um as Record<string, unknown>;
        usage.inputTokens += asInt(u.input_tokens);
        usage.outputTokens += asInt(u.output_tokens);
        usage.totalTokens += asInt(u.total_tokens);
        const itd = u.input_token_details;
        if (itd !== null && typeof itd === "object") {
          const d = itd as Record<string, unknown>;
          usage.cacheReadTokens += asInt(d.cache_read);
          usage.cacheCreationTokens += asInt(d.cache_creation);
        }
        const otd = u.output_token_details;
        if (otd !== null && typeof otd === "object") {
          usage.reasoningTokens += asInt((otd as Record<string, unknown>).reasoning);
        }
      }
    }
  }

  let latencyMs: number | null = null;
  if (events.length >= 2) {
    const first = Date.parse(events[0].receivedAt);
    const last = Date.parse(events[events.length - 1].receivedAt);
    if (!Number.isNaN(first) && !Number.isNaN(last) && last >= first) {
      latencyMs = last - first;
    }
  }

  return { finalText, reasoning, usage: reported ? usage : null, stepCount, latencyMs, finishReason, modelName };
}
