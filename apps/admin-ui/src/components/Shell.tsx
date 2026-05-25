import type { ReactNode } from "react";
import { Layout } from "antd";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

const { Sider, Header, Content } = Layout;

export function Shell({ children }: { children: ReactNode }) {
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
        <Content style={{ padding: 24, overflow: "auto" }}>
          <div style={{ maxWidth: 1280, margin: "0 auto" }}>{children}</div>
        </Content>
      </Layout>
    </Layout>
  );
}
