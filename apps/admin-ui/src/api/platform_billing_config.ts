/**
 * Platform billing-config SDK — backed by /v1/platform/billing-config
 * (Stream 12.4). system_admin-only, platform-level. One flag for now:
 * ``rollup_enabled`` — the offline billing-rollup job reads it before each run
 * and skips when false, so the platform can pause cost rollup from the admin UI
 * without touching the k8s CronJob.
 */
import { getJson, putJson } from "./client";

export interface PlatformBillingConfigView {
  rollup_enabled: boolean;
}

export async function getPlatformBillingConfig(): Promise<PlatformBillingConfigView> {
  return getJson<PlatformBillingConfigView>("/v1/platform/billing-config");
}

export async function putPlatformBillingConfig(
  rollupEnabled: boolean,
): Promise<PlatformBillingConfigView> {
  return putJson("/v1/platform/billing-config", {
    rollup_enabled: rollupEnabled,
  });
}
