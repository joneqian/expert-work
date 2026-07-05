/**
 * Prompt-cache hit rate — Stream RT-3 (RT-ADR-14).
 *
 * The four-segment token metering (input / output / cache_creation /
 * cache_read) is already carried end to end; the hit rate is derived purely on
 * the client so no backend metric is added (avoids drift with the metrics.py
 * validator). "Hit rate" = the share of input-side tokens served from cache:
 *
 *     cache_read / (input + cache_read + cache_creation)
 *
 * output_tokens are excluded (they are generated, never cached). cache_creation
 * is a full-price write that seeds the cache, so it sits in the denominator as
 * a not-yet-hit input-side token. A tenant that never reuses context reads 0%;
 * a warm long conversation trends high.
 *
 * Pure + side-effect free so it is trivially unit-testable.
 */

export interface CacheTokenCounts {
  input_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
}

/** Fraction in ``[0, 1]``, or ``null`` when there are no input-side tokens
 *  (an undefined rate — the caller renders it as ``—`` rather than ``0%``).
 *  Non-finite / negative inputs are coerced to 0 so a malformed payload never
 *  throws or yields a nonsense rate. */
export function cacheHitRate(counts: CacheTokenCounts): number | null {
  const read = safe(counts.cache_read_tokens);
  const denom = safe(counts.input_tokens) + read + safe(counts.cache_creation_tokens);
  if (denom <= 0) return null;
  return read / denom;
}

/** Render a ``cacheHitRate`` result as a percentage string with one decimal,
 *  e.g. ``0.732 -> "73.2%"``; ``null -> "—"``. */
export function formatHitRate(rate: number | null): string {
  if (rate === null) return "—";
  return `${(rate * 100).toFixed(1)}%`;
}

function safe(n: number): number {
  return Number.isFinite(n) && n > 0 ? n : 0;
}
