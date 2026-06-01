/**
 * Members SDK — backed by ``/v1/members`` (Stream R W2).
 *
 * Backend returns the ``{success, data, error}`` envelope. Tenant-scoped:
 * an admin manages members of their own tenant. Invitation is a batch
 * call returning per-item results so a partial failure (Keycloak
 * conflict / unavailable) surfaces without aborting the whole batch.
 */
import { apiClient, getJson, postJson } from "./client";

export type MemberRole = "admin" | "operator" | "viewer";

export type MemberStatus = "invited" | "active" | "suspended" | "revoked";

export interface TenantMember {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string | null;
  role: MemberRole;
  status: MemberStatus;
  keycloak_user_id: string | null;
  subject_id: string | null;
  invited_by: string;
  invited_at: string | null;
  activated_at: string | null;
  updated_at: string | null;
}

export interface MemberList {
  items: TenantMember[];
  total: number;
}

export interface InvitationItem {
  email: string;
  role: MemberRole;
  display_name?: string;
}

export interface InviteResultItem {
  email: string;
  member_id: string | null;
  status: string | null;
  error_code: string | null;
}

export interface InviteResult {
  results: InviteResultItem[];
}

export interface ResendResult {
  member_id: string;
  status: string;
  keycloak_user_id: string | null;
}

export interface ListMembersParams {
  status?: MemberStatus;
  limit?: number;
  offset?: number;
}

export async function listMembers(
  params: ListMembersParams = {},
): Promise<MemberList> {
  const { status, limit, offset } = params;
  return getJson<MemberList>("/v1/members", {
    params: { status, limit, offset },
  });
}

export async function inviteMembers(
  invitations: InvitationItem[],
): Promise<InviteResult> {
  return postJson<InviteResult>("/v1/members/invite", { invitations });
}

export async function resendMember(memberId: string): Promise<ResendResult> {
  return postJson<ResendResult>(
    `/v1/members/${encodeURIComponent(memberId)}/resend`,
    {},
  );
}

export async function revokeMember(memberId: string): Promise<void> {
  await apiClient.delete(`/v1/members/${encodeURIComponent(memberId)}`);
}
