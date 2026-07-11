/**
 * Pair a resumed thread's flat message history with its runs so each past
 * turn can be rebuilt as a full (lazy) TurnCard. The run event stream does
 * NOT carry the user input (it's the graph input, kept in the checkpoint),
 * so the input text comes from ``/messages`` here, paired to the run that
 * produced it by ORDER — user turn ``i`` ↔ ``runs[i]`` (runs oldest-first).
 *
 * ``is_resume`` is deliberately ignored: it means "not the thread's first
 * run", not "approval continuation", so it can't delimit turns. A count
 * mismatch (an approval that split one turn across 2 runs, an auto-triggered
 * or errored run) is the honest signal that order-pairing is unsafe — we
 * return ``null`` and the caller falls back to flat text.
 */
import type { HistoryMessage } from "../../../api/sessions";
import type { ThreadRunSummary } from "../../../api/runs";

export interface HistoryTurn {
  key: string;
  input: string;
  fallbackAnswer: string;
  runId: string;
  status: string;
}

export function buildHistoryTurns(
  messages: readonly HistoryMessage[],
  runs: readonly ThreadRunSummary[],
): HistoryTurn[] | null {
  const pairs: { input: string; answer: string }[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const m = messages[i];
    if (m.role !== "user") continue;
    const next = messages[i + 1];
    const answer = next && next.role === "assistant" ? next.content : "";
    pairs.push({ input: m.content, answer });
  }
  if (pairs.length !== runs.length) return null;
  return pairs.map((p, i) => ({
    key: runs[i].runId,
    input: p.input,
    fallbackAnswer: p.answer,
    runId: runs[i].runId,
    status: runs[i].status,
  }));
}
