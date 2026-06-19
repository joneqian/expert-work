import { useEffect, type ReactNode } from "react";
import { Layout } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { useAuth } from "../auth/AuthContext";
import { SCOPE_ALL, SCOPE_HOME, useTenantScope } from "../tenant/TenantScopeContext";
import { TENANT_LANDING, groupForPath, isPlatformScope } from "./navModel";

const { Sider, Header, Content } = Layout;

/**
 * Keep scope and route aligned, deep-link friendly (§4).
 *
 * The route's group implies an operating level; rather than bouncing the
 * user off a deep-linked page, we *align the scope* to the page:
 *
 *   - platform route + system_admin not yet at platform level → switch up
 *     to ``"*"`` and stay on the page (so a bookmark / direct link to a
 *     platform page just works).
 *   - platform route + non-admin → redirect to the workspace landing (no
 *     access; the page also gates server-side).
 *   - tenant route while at platform level → drop back to the home tenant
 *     and stay on the page.
 *
 * Routes whose scope already matches are left alone (no churn / no loop:
 * each branch makes the next render a no-op).
 */
function useScopeRedirect(): void {
  const { scope, setScope } = useTenantScope();
  const isSystemAdmin = useAuth().identity?.isSystemAdmin ?? false;
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const group = groupForPath(location.pathname);
    if (group === null) return; // not a grouped nav route — leave it be.
    if (group === "platform") {
      if (!isSystemAdmin) {
        navigate(TENANT_LANDING, { replace: true });
      } else if (!isPlatformScope(scope)) {
        setScope(SCOPE_ALL); // deep-link into a platform page → enter platform level
      }
    } else if (isPlatformScope(scope)) {
      setScope(SCOPE_HOME); // tenant page while at platform level → enter a tenant
    }
  }, [scope, isSystemAdmin, location.pathname, navigate, setScope]);
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
