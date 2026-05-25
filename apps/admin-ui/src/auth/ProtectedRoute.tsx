/**
 * ProtectedRoute — Stream H.1b.
 *
 * Wraps an authenticated subtree. When the auth state is still
 * resolving from localStorage we render a tiny skeleton placeholder
 * (no Antd Spin — keeps the login redirect snappy). Once resolved,
 * anonymous callers are redirected to ``/login`` with the original
 * path preserved in ``state.from`` so the post-login redirect can land
 * them back where they intended.
 */
import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "./AuthContext";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div
        aria-hidden
        data-testid="auth-loading"
        style={{ minHeight: 120, padding: 24, opacity: 0.4 }}
      />
    );
  }

  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
