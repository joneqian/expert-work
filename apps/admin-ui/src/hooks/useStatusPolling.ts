/**
 * Status polling hook — Stream H.3 PR 5 (Mini-ADR H-7).
 *
 * Polls a callback at a fixed interval while the run is non-terminal
 * AND the page is visible. The page-visibility gate matters because a
 * backgrounded tab keeps charging the user for backend calls without
 * any UI benefit; ``document.visibilityState`` flips back to the active
 * polling cadence the moment the user returns.
 *
 * Terminal-state stop is the second guarantee — once the run reaches a
 * terminal status (success / error / timeout / interrupted / paused
 * with no resume in flight) the timer is dropped. Callers that resume
 * the run reset the hook with the new status.
 */
import { useEffect, useRef } from "react";

import type { RunStatus } from "../api/runs";

/** Statuses the hook keeps polling on. Anything else stops the timer. */
const ACTIVE_STATUSES: ReadonlySet<RunStatus> = new Set<RunStatus>([
  "pending",
  "running",
  "paused",
  // legacy names kept for older clients hitting un-migrated rows
  "queued",
  "awaiting_approval",
]);

export interface UseStatusPollingOptions {
  status: RunStatus | null;
  /** Called every ``intervalMs`` while polling is active. Errors are
   *  swallowed by the caller; the hook doesn't catch. */
  onTick: () => void;
  intervalMs?: number;
}

export function useStatusPolling({
  status,
  onTick,
  intervalMs = 3000,
}: UseStatusPollingOptions): void {
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  useEffect(() => {
    if (status === null || !ACTIVE_STATUSES.has(status)) return;

    let cancelled = false;

    const tick = () => {
      if (cancelled) return;
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        onTickRef.current();
      }
    };

    const id = window.setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [status, intervalMs]);
}
