import { Empty } from "antd";
import { Construction } from "lucide-react";

export function ComingSoon({ title }: { title: string }) {
  return (
    <div>
      <div className="hx-page-header">
        <h1>{title}</h1>
      </div>
      <Empty
        image={<Construction size={48} strokeWidth={1.5} style={{ color: "var(--hx-text-tertiary)", margin: "0 auto" }} />}
        description={
          <>
            <div style={{ fontSize: 14, color: "var(--hx-text-primary)", marginBottom: 4 }}>
              此页面在正式 H.1b 实施中
            </div>
            <div style={{ fontSize: 13, color: "var(--hx-text-tertiary)" }}>
              demo 仅演示 4 个核心页面:Agents 列表 / Agent 详情(含 Playground)/ Run+Approval / Settings API Keys。<br />
              其他页面(<strong>{title}</strong>)将在 Stream H 全面落地。
            </div>
          </>
        }
        style={{ padding: "80px 24px" }}
      />
    </div>
  );
}
