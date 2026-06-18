import { useEffect, type ReactNode } from "react";
import { Layout } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useTenantScope } from "../tenant/TenantScopeContext";
import {
  PLATFORM_LANDING,
  TENANT_LANDING,
  groupForPath,
  isPlatformScope,
} from "./navModel";

const { Sider, Header, Content } = Layout;

/**
 * Keep the active route in a group the current scope can see (§4).
 *
 *   - switching up to the platform level while on a tenant route →
 *     redirect to the platform landing page.
 *   - switching back to a tenant while on a platform route → redirect to
 *     the workspace landing page.
 *
 * Routes already in a group the scope owns are left alone (no churn).
 */
function useScopeRedirect(): void {
  const { scope } = useTenantScope();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const group = groupForPath(location.pathname);
    if (group === null) return; // not a grouped nav route — leave it be.
    if (isPlatformScope(scope)) {
      if (group !== "platform") navigate(PLATFORM_LANDING, { replace: true });
    } else if (group === "platform") {
      navigate(TENANT_LANDING, { replace: true });
    }
  }, [scope, location.pathname, navigate]);
}

export function Shell({ children }: { children: ReactNode }) {
  useScopeRedirect();
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={220}
        style={{
          borderRight: "1px solid var(--hx-border-subtle)",
        }}
      >
        <Sidebar />
      </Sider>
      <Layout>
        <Header
          style={{
            borderBottom: "1px solid var(--hx-border-subtle)",
            display: "flex",
            alignItems: "center",
            gap: 16,
            padding: "0 24px",
          }}
        >
          <Topbar />
        </Header>
        <Content style={{ padding: "24px 32px", overflow: "auto" }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
