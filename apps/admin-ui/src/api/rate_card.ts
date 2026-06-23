/**
 * SDK for ``/v1/platform/rate-card`` — 模型定价简化.
 *
 * All endpoints are ENVELOPED (``{success,data,error}``) — calls go through the
 * ``getJson``/``postJson``/``patchJson`` helpers. DELETE is a bare 204.
 *
 * Authorization is a double gate server-side (``require("billing",·)`` +
 * ``is_system_admin``); the page's frontend gate is UX only.
 *
 * Prices are stored as integer **micro-元 per 百万 tokens** (``*_per_mtok_micros``).
 * The UI shows/inputs 元/百万tokens (decimals allowed); ``mtokMicrosToCny`` /
 * ``cnyToMtokMicros`` convert between the two — no float stored.
 */
import { apiClient, getJson, patchJson, postJson } from "./client";

export interface RateCardRecord {
  id: string;
  /** NULL = platform-global (the only shape today). */
  tenant_id: string | null;
  provider: string;
  model: string;
  input_per_mtok_micros: number;
  output_per_mtok_micros: number;
  cache_creation_per_mtok_micros: number;
  cache_read_per_mtok_micros: number;
}

export interface RateCardUpsert {
  provider: string;
  model: string;
  input_per_mtok_micros: number;
  output_per_mtok_micros: number;
  cache_creation_per_mtok_micros?: number;
  cache_read_per_mtok_micros?: number;
}

/** Mutable fields only — provider/model are the row's identity (one row per
 *  provider+model) and immutable post-create; reprice edits prices in place. */
export interface RateCardPatch {
  input_per_mtok_micros?: number;
  output_per_mtok_micros?: number;
  cache_creation_per_mtok_micros?: number;
  cache_read_per_mtok_micros?: number;
}

export async function listRateCards(
  params: { provider?: string; model?: string } = {},
): Promise<RateCardRecord[]> {
  return getJson<RateCardRecord[]>("/v1/platform/rate-card", {
    params: { provider: params.provider, model: params.model },
  });
}

export async function createRateCard(body: RateCardUpsert): Promise<RateCardRecord> {
  return postJson<RateCardRecord>("/v1/platform/rate-card", body);
}

export async function patchRateCard(id: string, body: RateCardPatch): Promise<RateCardRecord> {
  return patchJson<RateCardRecord>(`/v1/platform/rate-card/${encodeURIComponent(id)}`, body);
}

export async function deleteRateCard(id: string): Promise<void> {
  await apiClient.delete(`/v1/platform/rate-card/${encodeURIComponent(id)}`);
}

/** Store unit (micro-元 / 百万tokens) → display unit (元 / 百万tokens). */
export function mtokMicrosToCny(micros: number): number {
  return micros / 1_000_000;
}

/** Display unit (元 / 百万tokens) → store unit (micro-元 / 百万tokens, integer). */
export function cnyToMtokMicros(cny: number): number {
  return Math.round(cny * 1_000_000);
}
