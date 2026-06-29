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
  reasoningTokens: number;
}

export interface TurnSummary {
  /** Last AI message text content — the agent's answer (null if none yet). */
  finalText: string | null;
  /** ``reasoning_content`` blocks, in arrival order. */
  reasoning: string[];
  /** Token usage summed across the turn's AI messages (null if none reported). */
  usage: TurnUsage | null;
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
  const usage: TurnUsage = {
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    reasoningTokens: 0,
  };

  for (const evt of events) {
    if (evt.event !== "updates") continue;
    for (const m of messagesOf(evt.data)) {
      if (m.type !== "ai") continue;
      const text = textOf(m.content);
      if (text !== null) finalText = text; // last AI text wins

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
          usage.cacheReadTokens += asInt((itd as Record<string, unknown>).cache_read);
        }
        const otd = u.output_token_details;
        if (otd !== null && typeof otd === "object") {
          usage.reasoningTokens += asInt((otd as Record<string, unknown>).reasoning);
        }
      }
    }
  }

  return { finalText, reasoning, usage: reported ? usage : null };
}
