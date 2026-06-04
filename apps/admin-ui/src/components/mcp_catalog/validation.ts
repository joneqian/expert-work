/**
 * Client-side catalog validation — Stream W.
 *
 * Mirrors the backend invariant: a ``bearer`` connector must declare exactly
 * one ``secret`` field in its ``auth_schema``; a ``none`` connector must
 * declare zero secret fields. Surfacing the same rule inline keeps the form
 * honest before the 422 ``CATALOG_INVALID`` round-trip.
 */
import type { McpAuthType } from "../../api/mcp-servers";
import type { McpCatalogAuthField } from "../../api/mcp-catalog";

/** Returns an i18n key for the violated rule, or ``null`` when valid. */
export function validateAuthSchemaSecrets(
  authType: McpAuthType,
  fields: McpCatalogAuthField[],
): string | null {
  const secretCount = fields.filter((f) => f.kind === "secret").length;
  if (authType === "bearer" && secretCount !== 1) {
    return "mcp_catalog.guard_bearer_one_secret";
  }
  if (authType === "none" && secretCount !== 0) {
    return "mcp_catalog.guard_none_zero_secret";
  }
  return null;
}
