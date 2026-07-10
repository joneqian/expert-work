import type { TimelineItem } from "./timeline";

export type TimelineFilter = "all" | "tool" | "error" | "retry";

function haystack(it: TimelineItem): string {
  if (it.kind === "agent") {
    return [it.node, it.model ?? "", it.finishReason ?? "", it.reasoning ?? "", it.content ?? "",
      ...it.tools.map((t) => `${t.toolName} ${t.status}`)].join(" ").toLowerCase();
  }
  if (it.kind === "compaction" || it.kind === "retry" || it.kind === "error" ||
      it.kind === "approval" || it.kind === "end") {
    return it.text.toLowerCase();
  }
  const aux = it as Extract<TimelineItem, { kind: "memory_recall" | "planner" | "reflect" | "memory_writeback" | "workspace_ingest" }>;
  return `${aux.kind} ${aux.summary}`.toLowerCase();
}

function matchesType(it: TimelineItem, type: TimelineFilter): boolean {
  switch (type) {
    case "all": return true;
    case "tool": return it.kind === "agent" && it.tools.length > 0;
    case "error": return (it.kind === "agent" && it.hasError) || it.kind === "error";
    case "retry": return it.kind === "retry";
  }
}

export function filterTimeline(
  items: readonly TimelineItem[], type: TimelineFilter, query: string,
): TimelineItem[] {
  const q = query.trim().toLowerCase();
  return items.filter((it) => matchesType(it, type) && (q === "" || haystack(it).includes(q)));
}
