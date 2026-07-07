/**
 * First-run setup SDK — backed by ``/v1/setup`` (platform bootstrap).
 *
 * Both calls are **pre-authentication**: they run before any platform
 * system_admin exists, so no Bearer token is attached. The status probe
 * is unauthenticated; the bootstrap POST is gated by a one-time
 * ``X-Setup-Token`` header (the ``EXPERT_WORK_SETUP_TOKEN`` the operator
 * set at deploy), not by a session.
 *
 * The shared ``apiClient`` only *attaches* a Bearer when one is stored,
 * so it is safe to reuse here — the status probe / bootstrap simply
 * carry no Authorization header when the browser has no token. We go
 * through ``apiClient`` directly (rather than ``postJson``) for the POST
 * because we need a custom ``X-Setup-Token`` header; the envelope is
 * unwrapped the same way ``client.ts`` does.
 */
import { apiClient, getJson, unwrap, type ApiEnvelope } from "./client";

export interface SetupStatus {
  /** ``false`` means no platform system_admin exists yet → run the
   *  wizard. */
  initialized: boolean;
  /** ``false`` means the deploy did not configure a setup token, so the
   *  wizard cannot complete (operator must set
   *  ``EXPERT_WORK_SETUP_TOKEN``). */
  setup_enabled: boolean;
}

export interface RunSetupBody {
  admin_email: string;
  admin_password: string;
  admin_display_name?: string;
  platform_tenant_display_name?: string;
}

export interface RunSetupResult {
  tenant_id: string;
  subject_id: string;
}

/** Probe whether the platform still needs first-run bootstrap. */
export async function getSetupStatus(): Promise<SetupStatus> {
  return getJson<SetupStatus>("/v1/setup/status");
}

/** Create the first platform system_admin. ``token`` is sent as the
 *  one-time ``X-Setup-Token`` header (never as a Bearer). */
export async function runSetup(
  body: RunSetupBody,
  token: string,
): Promise<RunSetupResult> {
  const response = await apiClient.post<ApiEnvelope<RunSetupResult>>(
    "/v1/setup",
    body,
    { headers: { "X-Setup-Token": token } },
  );
  return unwrap(response.data);
}
