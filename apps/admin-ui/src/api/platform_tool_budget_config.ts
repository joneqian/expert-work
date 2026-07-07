/**
 * Platform tool-output-budget SDK — backed by /v1/platform/tool-budget-config
 * (Phase 3). system_admin-only, platform-level. One flag: the master on/off for
 * the tool-output-budget feature (generalized externalization + persist floor +
 * CM-12 prune). ``enabled`` is the explicit platform override (``null`` ⇒ unset,
 * using the ``EXPERT_WORK_TOOL_OUTPUT_BUDGET`` env default); ``effective`` is the
 * resolved on/off the agent build reads.
 */
import { getJson, putJson } from "./client";

export interface PlatformToolBudgetConfigView {
  /** Explicit platform override; ``null`` when unset (→ env default). */
  enabled: boolean | null;
  /** Resolved on/off (DB row if set, else the env default). */
  effective: boolean;
}

export async function getPlatformToolBudgetConfig(): Promise<PlatformToolBudgetConfigView> {
  return getJson<PlatformToolBudgetConfigView>("/v1/platform/tool-budget-config");
}

export async function putPlatformToolBudgetConfig(
  enabled: boolean,
): Promise<PlatformToolBudgetConfigView> {
  return putJson("/v1/platform/tool-budget-config", { enabled });
}
