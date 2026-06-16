/**
 * Role Bindings SDK — backed by ``/v1/role_bindings`` (Stream C.3 +
 * Stream N).
 *
 * Backend returns the ``{success, data, error}`` envelope. Two key
 * special cases per Stream N:
 *
 *   - ``platform_scope=true`` requires ``role="system_admin"`` (DTO
 *     validator) AND the caller must be a system_admin themselves
 *     (backend gate — see ``role_bindings.py:66``). Frontend mirrors
 *     this by hiding the checkbox unless the caller is a system_admin.
 *   - ``listRoleBindings({platformScope: true})`` is a platform-admin
 *     view; non-system-admins get 403 before scope resolution.
 */
import { apiClient, getJson, postJson, withTenantScope, type TenantScope } from "./client";

export type RoleName = "tenant_admin" | "developer" | "viewer" | "system_admin";

export type SubjectType = "user" | "service_account";

/**
 * Stream 8.5 — ABAC conditions narrowing a tenant-scope binding to matching
 * resource instances. All three predicates combine with AND; an absent /
 * all-empty value (or ``null``) means the grant is type-wide (unconditioned).
 */
export interface BindingConditions {
  /** URI-level allowlist of resource ids / names (empty ⇒ any). */
  resource_ids?: string[];
  /** Resource ``metadata.labels`` superset match (empty ⇒ any). */
  labels?: Record<string, string>;
  /** Restrict to resources whose owner is the binding subject. */
  owner_only?: boolean;
}

export interface RoleBindingRecord {
  id: string;
  /** ``null`` for platform-scope bindings (no tenant). */
  tenant_id: string | null;
  subject_type: SubjectType;
  subject_id: string;
  role: RoleName;
  platform_scope: boolean;
  /** Stream 8.5 — ABAC narrowing; ``null`` ⇒ unconditioned (type-wide). */
  conditions?: BindingConditions | null;
  granted_by: string;
  granted_at: string;
}

export interface RoleBindingList {
  items: RoleBindingRecord[];
  total: number;
  cross_tenant: boolean;
}

export interface ListRoleBindingsParams {
  tenantScope?: TenantScope;
  /** ``true`` lists platform-scope bindings; requires system_admin. */
  platformScope?: boolean;
}

export async function listRoleBindings(
  params: ListRoleBindingsParams = {},
): Promise<RoleBindingList> {
  const { tenantScope, platformScope } = params;
  const query = withTenantScope(
    { platform_scope: platformScope },
    tenantScope,
  );
  return getJson<RoleBindingList>("/v1/role_bindings", { params: query });
}

export interface CreateRoleBindingBody {
  subject_type: SubjectType;
  subject_id: string;
  role: RoleName;
  platform_scope?: boolean;
  /** Stream 8.5 — tenant-scope only; omit for an unconditioned grant. */
  conditions?: BindingConditions | null;
}

export async function createRoleBinding(
  body: CreateRoleBindingBody,
): Promise<RoleBindingRecord> {
  return postJson<RoleBindingRecord>("/v1/role_bindings", body);
}

export async function deleteRoleBinding(bindingId: string): Promise<void> {
  await apiClient.delete(`/v1/role_bindings/${encodeURIComponent(bindingId)}`);
}
