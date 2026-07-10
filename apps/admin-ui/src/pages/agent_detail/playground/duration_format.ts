/**
 * Human-readable per-step / per-tool duration. Adaptive units:
 * sub-second → integer ms; < 1min → seconds (1 decimal); else minutes+seconds.
 * Batch 4a — fills the StepTimeline duration slot Batch 3 left empty.
 */
export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  let minutes = Math.floor(ms / 60000);
  let seconds = Math.round((ms % 60000) / 1000);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return `${minutes}m${seconds}s`;
}
