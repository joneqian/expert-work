/**
 * Tenant scope context — Stream H.1b (Stream N integration).
 *
 * The current tenant scope is one of:
 *
 *   - ``home`` — caller's home tenant (default for everyone).
 *   - ``"*"`` — cross-tenant aggregate (system_admin only; the
 *     ``ensure_tenant_scope`` middleware on the control-plane rejects
 *     anything else with 403 ``CROSS_TENANT_FORBIDDEN``).
 *   - specific UUID — system_admin "switch to tenant X" view; also
 *     audited server-side.
 *
 * Pages call :func:`useTenantScope` and thread ``scope`` through the
 * SDK via :func:`withTenantScope` from ``api/client``. Persisting the
 * scope to sessionStorage keeps the choice across reloads inside one
 * tab but isolates from other admin sessions.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { useAuth } from "../auth/AuthContext";

export const SCOPE_HOME = "home" as const;
export const SCOPE_ALL = "*" as const;

/** ``"home"`` ⇒ omit ``tenant_id`` on the wire; ``"*"`` ⇒ cross-tenant;
 *  any UUID ⇒ specific tenant switch. */
export type TenantScopeValue = typeof SCOPE_HOME | typeof SCOPE_ALL | string;

interface TenantScopeContextValue {
  scope: TenantScopeValue;
  setScope: (next: TenantScopeValue) => void;
  /** Translated for the SDK: ``undefined`` for home, ``"*"`` for all,
   *  or the UUID for a specific tenant. */
  apiTenantScope: undefined | string;
}

const TenantScopeContext = createContext<TenantScopeContextValue | null>(null);

const STORAGE_KEY = "helix.admin.tenantScope";

function readStored(): TenantScopeValue | null {
  if (typeof window === "undefined") return null;
  const value = window.sessionStorage.getItem(STORAGE_KEY);
  if (value === null) return null;
  if (value === SCOPE_HOME || value === SCOPE_ALL) return value;
  return value;
}

function writeStored(scope: TenantScopeValue): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(STORAGE_KEY, scope);
}

export function TenantScopeProvider({ children }: { children: ReactNode }) {
  const { identity } = useAuth();
  const isSystemAdmin = identity?.isSystemAdmin ?? false;

  const [scope, setScopeState] = useState<TenantScopeValue>(() => {
    const stored = readStored();
    if (stored !== null) return stored;
    return SCOPE_HOME;
  });

  // Demote stale scope when a non-system-admin lands with a cached
  // ``"*"`` value (eg. operator left a JWT in localStorage from a
  // previous system-admin session on this machine).
  useEffect(() => {
    if (!isSystemAdmin && scope === SCOPE_ALL) {
      setScopeState(SCOPE_HOME);
      writeStored(SCOPE_HOME);
    }
  }, [isSystemAdmin, scope]);

  const setScope = useCallback(
    (next: TenantScopeValue) => {
      if (next === SCOPE_ALL && !isSystemAdmin) {
        // Defense in depth: UI should never offer "*" to a non-admin,
        // but if some path slips through, refuse silently rather than
        // calling the API and getting a 403.
        return;
      }
      setScopeState(next);
      writeStored(next);
    },
    [isSystemAdmin],
  );

  const apiTenantScope = useMemo<undefined | string>(() => {
    if (scope === SCOPE_HOME) return undefined;
    return scope;
  }, [scope]);

  const value = useMemo<TenantScopeContextValue>(
    () => ({ scope, setScope, apiTenantScope }),
    [scope, setScope, apiTenantScope],
  );

  return (
    <TenantScopeContext.Provider value={value}>{children}</TenantScopeContext.Provider>
  );
}

export function useTenantScope(): TenantScopeContextValue {
  const ctx = useContext(TenantScopeContext);
  if (ctx === null) {
    throw new Error("useTenantScope() must be called inside <TenantScopeProvider>");
  }
  return ctx;
}
