/**
 * Skill API adapter — skill-authoring-ia Phase B.
 *
 * ``SkillDetail`` and its sub-components (``FileEditor`` / ``AddFileModal`` /
 * ``RenameDeleteModals``) used to import the *tenant* ``./skills`` SDK
 * directly, which hard-wired the detail page to ``/v1/skills``. To reuse the
 * same editor for the platform skill library (Phase C), they now take an
 * injected :class:`SkillApi` so the transport (tenant vs platform) is a
 * constructor choice, not a hard import.
 *
 * Two implementations:
 *   - :data:`tenantSkillApi` — thin pass-through to ``./skills`` (`/v1/skills`)
 *   - :data:`platformSkillApi` — wraps ``./platform-skills`` (`/v1/platform/skills`)
 *
 * The platform types (``PlatformSkill`` / ``PlatformSkillVersion``) are
 * structurally a subset of the tenant ``SkillRecord`` / ``SkillVersion``
 * (same field names; platform ``status`` omits ``"stale"``, which is a subset
 * of the tenant union), so the platform impl is assignable without an explicit
 * field-by-field mapping. The base64 codec helpers (``encodeUtf8Base64`` /
 * ``decodeBase64Utf8``) are pure and identical for both, so callers keep
 * importing them from ``./skills`` directly.
 */
import {
  getSkill,
  listSkillVersions,
  patchSkillStatus,
  exportSkillVersion,
  getSupportingFile,
  putSupportingFile,
  deleteSupportingFile,
  renameSupportingFile,
  type SkillRecord,
  type SkillVersion,
  type SkillVersionList,
  type SupportingFileBody,
  type PatchSkillStatusBody,
} from "./skills";
import {
  getPlatformSkill,
  listPlatformSkillVersions,
  patchPlatformSkill,
  exportPlatformSkillVersion,
  getPlatformSupportingFile,
  putPlatformSupportingFile,
  deletePlatformSupportingFile,
} from "./platform-skills";

export interface SkillApi {
  getSkill(id: string): Promise<SkillRecord>;
  listVersions(id: string): Promise<SkillVersionList>;
  patchStatus(id: string, body: PatchSkillStatusBody): Promise<SkillRecord>;
  exportVersion(id: string, version: number): Promise<Blob>;
  getSupportingFile(
    id: string,
    version: number,
    filePath: string,
  ): Promise<SupportingFileBody>;
  putSupportingFile(
    id: string,
    version: number,
    filePath: string,
    body: SupportingFileBody,
  ): Promise<SkillVersion>;
  deleteSupportingFile(
    id: string,
    version: number,
    filePath: string,
  ): Promise<SkillVersion>;
  renameSupportingFile(
    id: string,
    version: number,
    oldPath: string,
    newPath: string,
    body: SupportingFileBody,
  ): Promise<SkillVersion>;
}

/** Tenant ``/v1/skills`` — thin pass-through. */
export const tenantSkillApi: SkillApi = {
  getSkill,
  listVersions: listSkillVersions,
  patchStatus: patchSkillStatus,
  exportVersion: exportSkillVersion,
  getSupportingFile,
  putSupportingFile,
  deleteSupportingFile,
  renameSupportingFile,
};

/** Platform ``/v1/platform/skills`` — structurally-compatible wrapper. The
 *  platform SDK has no single ``rename`` call (server has no rename route), so
 *  it is composed put-new + delete-old, mirroring the tenant SDK's helper
 *  (put first so a failure leaves the original intact). */
export const platformSkillApi: SkillApi = {
  getSkill: getPlatformSkill,
  listVersions: listPlatformSkillVersions,
  patchStatus: patchPlatformSkill,
  exportVersion: exportPlatformSkillVersion,
  getSupportingFile: getPlatformSupportingFile,
  putSupportingFile: putPlatformSupportingFile,
  deleteSupportingFile: deletePlatformSupportingFile,
  renameSupportingFile: async (id, version, oldPath, newPath, body) => {
    const afterPut = await putPlatformSupportingFile(id, version, newPath, {
      content: body.content,
      size: body.size,
      mime: body.mime,
    });
    return deletePlatformSupportingFile(id, afterPut.version, oldPath);
  },
};
