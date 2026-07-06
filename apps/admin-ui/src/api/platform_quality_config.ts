/**
 * Platform quality-monitor config SDK — backed by /v1/platform/quality-config
 * (Stream RT-5 PR-3b/3c). system_admin-only, platform-level. ``enabled`` is the
 * operational on/off (ANDed with the deploy gate); it defaults off (judge tokens
 * cost money) so a fresh platform turns monitoring on here.
 */
import { getJson, putJson } from "./client";

export interface QualityConfig {
  enabled: boolean;
  sampling_rate_pct: number;
  daily_cap: number;
  monitor_interval_s: number;
  monitor_batch_size: number;
  judge_provider: string;
  judge_model: string;
  drift_interval_s: number;
  drift_recent_window_h: number;
  drift_baseline_window_h: number;
  drift_min_samples: number;
  drift_threshold: number;
  drift_cooldown_h: number;
}

export interface PlatformQualityConfigView {
  config: QualityConfig;
  /** True while no row is saved yet — the shown values are env defaults. */
  is_default: boolean;
}

export async function getPlatformQualityConfig(): Promise<PlatformQualityConfigView> {
  return getJson<PlatformQualityConfigView>("/v1/platform/quality-config");
}

export async function putPlatformQualityConfig(
  body: QualityConfig,
): Promise<{ config: QualityConfig }> {
  return putJson("/v1/platform/quality-config", body);
}
