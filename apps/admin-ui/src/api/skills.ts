/**
 * Skills SDK — backed by ``/v1/skills`` (Stream J.7a).
 *
 * Stream H.1b PR 3 added the list-only skeleton; Stream H.4 PR 5 fills
 * in the full surface (create / version / status patch / ZIP import +
 * export) plus a latent bug fix:
 *
 * **Latent bug fix (H.4 PR 5)**: H.1b PR 3 used ``getJson`` which
 * unwraps a ``{success, data, error}`` envelope, but the skills
 * backend returns raw ``JSONResponse(content={...})`` payloads —
 * matches the curation latent-bug fix in H.4 PR 1. The SDK now goes
 * through ``apiClient`` directly.
 *
 * Also: cursor pagination (no ``total``), so the list response
 * exposes ``next_cursor`` + ``cross_tenant`` only.
 */
import { apiClient, withTenantScope, type TenantScope } from "./client";

export type SkillStatus = "draft" | "active" | "archived";

export interface SkillRecord {
  id: string;
  name: string;
  status: SkillStatus;
  latest_version: number | null;
  description: string;
  category: string;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  id: string;
  skill_id: string;
  version: number;
  prompt_fragment: string;
  tool_names: string[];
  description: string;
  category: string;
  required_models: string[];
  authored_by: string;
  created_at: string;
}

export interface SkillList {
  items: SkillRecord[];
  /** Opaque UUID-encoded cursor; pass back verbatim. */
  next_cursor: string | null;
  cross_tenant: boolean;
}

export interface ListSkillsParams {
  tenantScope?: TenantScope;
  status?: SkillStatus;
  category?: string;
  cursor?: string | null;
  limit?: number;
}

export async function listSkills(
  params: ListSkillsParams = {},
): Promise<SkillList> {
  const { tenantScope, status, category, cursor, limit } = params;
  const query = withTenantScope(
    { status, category, cursor: cursor ?? undefined, limit },
    tenantScope,
  );
  const response = await apiClient.get<SkillList>("/v1/skills", { params: query });
  return response.data;
}

export async function getSkill(skillId: string): Promise<SkillRecord> {
  const response = await apiClient.get<SkillRecord>(
    `/v1/skills/${encodeURIComponent(skillId)}`,
  );
  return response.data;
}

export interface CreateSkillBody {
  name: string;
  description: string;
  category: string;
}

export async function createSkill(body: CreateSkillBody): Promise<SkillRecord> {
  const response = await apiClient.post<SkillRecord>("/v1/skills", body);
  return response.data;
}

export interface PatchSkillStatusBody {
  status: SkillStatus;
}

export async function patchSkillStatus(
  skillId: string,
  body: PatchSkillStatusBody,
): Promise<SkillRecord> {
  const response = await apiClient.patch<SkillRecord>(
    `/v1/skills/${encodeURIComponent(skillId)}`,
    body,
  );
  return response.data;
}

export interface AddSkillVersionBody {
  prompt_fragment: string;
  tool_names: string[];
  description: string;
  category: string;
  required_models?: string[];
  authored_by?: string;
}

export async function addSkillVersion(
  skillId: string,
  body: AddSkillVersionBody,
): Promise<SkillVersion> {
  const response = await apiClient.post<SkillVersion>(
    `/v1/skills/${encodeURIComponent(skillId)}/versions`,
    body,
  );
  return response.data;
}

export interface SkillVersionList {
  items: SkillVersion[];
}

export async function listSkillVersions(skillId: string): Promise<SkillVersionList> {
  const response = await apiClient.get<SkillVersionList>(
    `/v1/skills/${encodeURIComponent(skillId)}/versions`,
  );
  return response.data;
}

export async function getSkillVersion(
  skillId: string,
  versionNumber: number,
): Promise<SkillVersion> {
  const response = await apiClient.get<SkillVersion>(
    `/v1/skills/${encodeURIComponent(skillId)}/versions/${versionNumber}`,
  );
  return response.data;
}

export interface ImportSkillZipResponse {
  skill: SkillRecord;
  version: SkillVersion;
}

/** Multipart upload of a ``.skill`` ZIP — creates the skill (if absent)
 *  + appends a new version. */
export async function importSkillZip(file: File | Blob): Promise<ImportSkillZipResponse> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<ImportSkillZipResponse>(
    "/v1/skills/import",
    form,
    {
      // Let axios + the browser pick the multipart boundary.
      headers: { "Content-Type": "multipart/form-data" },
    },
  );
  return response.data;
}

/** Download a versioned skill as a ``.skill`` ZIP. Returns the raw
 *  ``Blob`` so the caller can drive an ``<a download>`` flow. */
export async function exportSkillVersion(
  skillId: string,
  versionNumber: number,
): Promise<Blob> {
  const response = await apiClient.get<Blob>(
    `/v1/skills/${encodeURIComponent(skillId)}/versions/${versionNumber}/export`,
    { responseType: "blob" },
  );
  return response.data;
}
