/**
 * Platform Skill SDK — backed by ``/v1/platform/skills`` (Stream X,
 * system_admin only).
 *
 * A *platform skill* is a curated, cross-tenant reusable skill managed by
 * a platform admin: it carries a lifecycle (draft / active / archived), a
 * ``required_tier`` entitlement gate, and an immutable version history of
 * prompt fragments + tool allow-lists. Tenants see active platform skills
 * merged into their own library (see ``GET /v1/skills`` ``platform_items``)
 * but cannot mutate them.
 *
 * Backend returns the standard ``{success, data, error}`` envelope; the
 * unwrapped payload is typed below. ``getJson`` / ``postJson`` /
 * ``patchJson`` call ``unwrap()`` internally — callers receive the data
 * directly. (Contrast with the *tenant* ``/v1/skills`` SDK in
 * ``./skills.ts``, which reads raw un-enveloped payloads.)
 */
import { getJson, patchJson, postJson } from "./client";

// ── Domain types ─────────────────────────────────────────────────────────

export type PlatformSkillStatus = "draft" | "active" | "archived";
export type PlatformSkillTier = "free" | "pro" | "enterprise";

export interface PlatformSkill {
  id: string;
  name: string;
  status: PlatformSkillStatus;
  latest_version: number | null;
  description: string;
  category: string;
  pinned: boolean;
  required_tier: PlatformSkillTier;
  last_used_at: string | null;
  state_changed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlatformSkillVersion {
  id: string;
  skill_id: string;
  version: number;
  prompt_fragment: string;
  tool_names: string[];
  description: string;
  category: string;
  required_models: string[];
  authored_by: string;
  supporting_files: Record<string, { size: number; mime: string }>;
  lazy_load: boolean;
  high_risk: boolean;
  created_at: string;
}

export interface ListPlatformSkillsParams {
  status?: PlatformSkillStatus;
  category?: string;
  cursor?: string | null;
  limit?: number;
}

export interface PlatformSkillList {
  items: PlatformSkill[];
  next_cursor: string | null;
}

export interface CreatePlatformSkillBody {
  name: string;
  description?: string;
  category?: string;
  required_tier?: PlatformSkillTier;
}

export interface AddPlatformSkillVersionBody {
  prompt_fragment: string;
  tool_names?: string[];
  description?: string;
  category?: string;
  required_models?: string[];
  authored_by?: string;
}

export interface PatchPlatformSkillBody {
  status?: PlatformSkillStatus;
  pinned?: boolean;
}

export interface PlatformSkillVersionList {
  items: PlatformSkillVersion[];
}

// ── Endpoints ──────────────────────────────────────────────────────────────

/** ``GET /v1/platform/skills`` — cursor-paginated platform skill list. */
export async function listPlatformSkills(
  params: ListPlatformSkillsParams = {},
): Promise<PlatformSkillList> {
  const { status, category, cursor, limit } = params;
  return getJson<PlatformSkillList>("/v1/platform/skills", {
    params: { status, category, cursor: cursor ?? undefined, limit },
  });
}

/** ``GET /v1/platform/skills/{id}`` — single platform skill (404 unknown). */
export async function getPlatformSkill(id: string): Promise<PlatformSkill> {
  return getJson<PlatformSkill>(`/v1/platform/skills/${encodeURIComponent(id)}`);
}

/** ``POST /v1/platform/skills`` — create a platform skill (409 duplicate). */
export async function createPlatformSkill(
  body: CreatePlatformSkillBody,
): Promise<PlatformSkill> {
  return postJson<PlatformSkill>("/v1/platform/skills", body);
}

/** ``POST /v1/platform/skills/{id}/versions`` — append a version
 *  (400 moderation/threat, 404 unknown skill). */
export async function addPlatformSkillVersion(
  id: string,
  body: AddPlatformSkillVersionBody,
): Promise<PlatformSkillVersion> {
  return postJson<PlatformSkillVersion>(
    `/v1/platform/skills/${encodeURIComponent(id)}/versions`,
    body,
  );
}

/** ``PATCH /v1/platform/skills/{id}`` — set status and/or pinned. */
export async function patchPlatformSkill(
  id: string,
  body: PatchPlatformSkillBody,
): Promise<PlatformSkill> {
  return patchJson<PlatformSkill>(
    `/v1/platform/skills/${encodeURIComponent(id)}`,
    body,
  );
}

/** ``GET /v1/platform/skills/{id}/versions`` — version history. */
export async function listPlatformSkillVersions(
  id: string,
): Promise<PlatformSkillVersionList> {
  return getJson<PlatformSkillVersionList>(
    `/v1/platform/skills/${encodeURIComponent(id)}/versions`,
  );
}
