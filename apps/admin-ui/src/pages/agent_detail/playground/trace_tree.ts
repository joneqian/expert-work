/**
 * Pure tree-flattening for the trace waterfall (Batch 4b). Extracted from
 * TraceView so the preorder walk + sibling ordering can be unit-tested
 * independently of the render.
 */
import type { TraceSpan } from "../../../api/trace_facade";

export interface TraceRowData {
  span: TraceSpan;
  depth: number;
  /** One entry per ancestor level (length === depth). Column `i < depth-1`
   *  draws a through-line when `continues[i]` is true (that ancestor has
   *  more siblings below); column `depth-1` always draws the elbow
   *  connecting the immediate parent down to this row. */
  continues: boolean[];
}

/**
 * Flatten the parentId-linked spans into an ordered, indented row list:
 * preorder (parent before its children), siblings sorted by `startMs`, each
 * row carrying the depth + per-ancestor `continues` flags the tree guides need.
 */
export function buildRows(spans: readonly TraceSpan[]): TraceRowData[] {
  const byParent = new Map<string | null, TraceSpan[]>();
  for (const span of spans) {
    const list = byParent.get(span.parentId) ?? [];
    list.push(span);
    byParent.set(span.parentId, list);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.startMs - b.startMs);
  }

  const rows: TraceRowData[] = [];
  function walk(parentId: string | null, continues: boolean[]): void {
    const children = byParent.get(parentId) ?? [];
    children.forEach((span, i) => {
      const isLast = i === children.length - 1;
      rows.push({ span, depth: continues.length, continues });
      walk(span.id, [...continues, !isLast]);
    });
  }
  walk(null, []);
  return rows;
}

/** A gantt bar is "wide" (duration label sits inside it) once it spans at
 *  least half the timeline; narrower bars put the label to their right. */
export function isWideBar(widthPct: number): boolean {
  return widthPct >= 50;
}
