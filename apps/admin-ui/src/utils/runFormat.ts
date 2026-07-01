/**
 * Shared run/conversation formatters — duration + compact token counts.
 *
 * Extracted so the conversation views (list tab + detail) and the runs
 * list render durations and token magnitudes identically.
 */

type TFn = (key: string, opts?: Record<string, unknown>) => string;

/** Compact wall-clock duration from a created→finished span. No
 *  ``finishedIso`` → still in flight, localized "running". */
export function formatDuration(
  t: TFn,
  createdIso: string | null,
  finishedIso: string | null,
): string {
  if (!createdIso) return "—";
  if (!finishedIso) return t("runs_page.duration_running");
  const seconds = Math.max(
    0,
    Math.round((new Date(finishedIso).getTime() - new Date(createdIso).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** 1234 → "1.2k", 2_000_000 → "2.0M" — keeps token columns narrow. */
export function formatCompact(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
