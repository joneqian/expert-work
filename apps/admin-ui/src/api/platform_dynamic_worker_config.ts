/**
 * Platform dynamic-worker limits SDK — backed by
 * /v1/platform/dynamic-worker-config (B3 PR2). system_admin-only,
 * platform-level. Three guardrails for the ``dynamic_worker`` tool
 * (spawn_worker): the per-run concurrency cap, the per-run cumulative spawn
 * cap, and the per-worker step cap. ``configured`` is the explicit platform
 * override (``null`` ⇒ unset, using the process's env-default settings
 * snapshot); ``effective`` is the resolved limits the agent build reads.
 */
import { getJson, putJson } from "./client";

export interface DynamicWorkerLimits {
  max_concurrent: number;
  max_per_run: number;
  max_iterations: number;
}

export interface PlatformDynamicWorkerConfigView {
  /** Explicit platform override; ``null`` when unset (→ env default). */
  configured: DynamicWorkerLimits | null;
  /** Resolved limits (DB row if set, else the env default). */
  effective: DynamicWorkerLimits;
}

export async function getPlatformDynamicWorkerConfig(): Promise<PlatformDynamicWorkerConfigView> {
  return getJson<PlatformDynamicWorkerConfigView>("/v1/platform/dynamic-worker-config");
}

export async function putPlatformDynamicWorkerConfig(
  limits: DynamicWorkerLimits,
): Promise<PlatformDynamicWorkerConfigView> {
  return putJson("/v1/platform/dynamic-worker-config", limits);
}
