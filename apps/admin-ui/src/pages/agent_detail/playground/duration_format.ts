/**
 * Human-readable per-step / per-tool duration. Adaptive units:
 * sub-second → integer ms; < 1min → seconds (1 decimal); else minutes+seconds.
 * Batch 4a — fills the StepTimeline duration slot Batch 3 left empty.
 */
export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) {
    const secs = (ms / 1000).toFixed(1);
    // 59950–59999ms rounds up to "60.0s"; fall through to m/s so it reads "1m0s".
    if (secs !== "60.0") return `${secs}s`;
  }
  let minutes = Math.floor(ms / 60000);
  let seconds = Math.round((ms % 60000) / 1000);
  if (seconds === 60) {
    minutes += 1;
    seconds = 0;
  }
  return `${minutes}m${seconds}s`;
}
