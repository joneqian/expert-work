import { useEffect, type ReactNode } from "react";
import { Layout } from "antd";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { SCOPE_ALL, useTenantScope } from "../tenant/TenantScopeContext";
import { groupForPath, isPlatformScope } from "./navModel";

const { Sider, Header, Content } = Layout;

/**
 * Enter the platform level when a system_admin deep-links a platform page
 * (§4, deep-link friendly). Minimal on purpose:
 *
 *   - platform route + system_admin not yet at platform level → switch up
 *     to ``"*"`` and stay on the page (bookmark / direct link just works,
 *     and the sidebar swaps to the platform group).
 *
 * Everything else is left to the pages themselves: non-admins on a platform
 * route get the page's own system-admin-only notice (no bounce); pages that
 * adapt to scope (e.g. cross-tenant Members at ``"*"``) keep whatever scope
 * the switcher set. No tenant-route force-switch — it would clobber those.
 */
function useScopeRedirect(): void {
  const { scope, setScope } = useTenantScope();
  const isSystemAdmin = useAuth().identity?.isSystemAdmin ?? false;
  const location = useLocation();

  useEffect(() => {
    if (
      groupForPath(location.pathname) === "platform" &&
      isSystemAdmin &&
      !isPlatformScope(scope)
    ) {
      setScope(SCOPE_ALL);
    }
  }, [scope, isSystemAdmin, location.pathname, setScope]);
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
