/**
 * Agent detail page — Stream H.1b PR 3 (scaffold) + H.2 PR 1 (Manifest
 * Monaco editor) + H.2 PR 3 (Playground tab).
 *
 * Real fetch of ``GET /v1/agents/{name}/{version}``. Route shape moved
 * from the demo's ``/agents/:agentId/:tab`` (mock id) to the canonical
 * ``/agents/:name/:version/:tab`` to match the backend identity
 * (Mini-ADR B-3 keeps ``name + version`` as the agent's natural key —
 * the row UUID is internal).
 *
 * Wired tabs: ``overview`` / ``manifest`` / ``playground``. The
 * remaining per-agent sub-views (Runs / Skills / Triggers / Memory)
 * land in Stream H.3 / H.4 — they need separate list-filter parameters
 * the backend list endpoints don't accept yet (eg. ``GET /v1/runs?
 * agent_name=…``).
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Breadcrumb,
  Card,
  Col,
  Empty,
  Row,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { Bot, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { getAgent, type AgentDetailResponse } from "../api/agents";
import { ApiError } from "../api/client";
import { ManifestTab } from "./agent_detail/ManifestTab";
import { PlaygroundTab } from "./agent_detail/PlaygroundTab";

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  active: "success",
  draft: "warning",
  archived: "default",
  deleted: "error",
};

export function AgentDetail() {
  const { t } = useTranslation();
  const { name, version, tab } = useParams<{
    name: string;
    version: string;
    tab?: string;
  }>();
  const nav = useNavigate();

  const [detail, setDetail] = useState<AgentDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!name || !version) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getAgent(name, version);
      setDetail(result);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [name, version]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const activeTab = tab ?? "overview";

  if (!name || !version) {
    return <Empty description="Missing :name or :version in URL" style={{ marginTop: 80 }} />;
  }

  if (loading) {
    return (
      <div>
        <Skeleton.Input active size="large" style={{ marginBottom: 16 }} />
        <Skeleton active paragraph={{ rows: 6 }} />
      </div>
    );
  }

  if (error !== null || detail === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("agent_detail.failed_to_load")}
        description={error ?? "agent not found"}
        data-testid="agent-detail-error"
        style={{ marginTop: 16 }}
      />
    );
  }

  const record = detail.record;

  return (
    <div data-testid="agent-detail-root">
      <Breadcrumb
        items={[
          { title: <Link to="/agents">{t("agents_page.page_title")}</Link> },
          { title: record.name },
        ]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={
          <ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />
        }
      />

      <div style={{ display: "flex", alignItems: "flex-start", gap: 16, paddingBottom: 16 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 6,
            background: "var(--hx-surface-selected)",
            color: "var(--hx-color-brand-500)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <Bot size={20} strokeWidth={1.5} />
        </div>
        <div style={{ flex: 1 }}>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 600, lineHeight: 1.2 }}>
            {record.name}
          </h1>
          <Space size={12} style={{ marginTop: 6, fontSize: 13, color: "var(--hx-text-secondary)" }}>
            <Tag color={STATUS_COLOR[record.status] ?? "default"} bordered={false}>
              {record.status}
            </Tag>
            <Text code style={{ fontSize: 12 }}>
              v{record.version}
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.spec_sha256.slice(0, 12)}…
            </Text>
          </Space>
        </div>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={(k) =>
          nav(`/agents/${encodeURIComponent(name)}/${encodeURIComponent(version)}/${k}`)
        }
        items={[
          { key: "overview", label: t("agent_detail.tab_overview") },
          { key: "manifest", label: t("agent_detail.tab_manifest") },
          { key: "playground", label: t("agent_detail.tab_playground") },
          { key: "runs", label: t("agent_detail.tab_runs") },
          { key: "skills", label: t("agent_detail.tab_skills") },
          { key: "triggers", label: t("agent_detail.tab_triggers") },
          { key: "memory", label: t("agent_detail.tab_memory") },
        ]}
      />

      {activeTab === "overview" && <OverviewTab detail={detail} />}
      {activeTab === "manifest" && <ManifestTab detail={detail} onSaved={refresh} />}
      {activeTab === "playground" && <PlaygroundTab detail={detail} />}
      {!["overview", "manifest", "playground"].includes(activeTab) && (
        <Empty
          description={t("agent_detail.tab_coming_soon", { tab: activeTab })}
          style={{ marginTop: 64 }}
          data-testid="agent-detail-tab-placeholder"
        />
      )}
    </div>
  );
}

function OverviewTab({ detail }: { detail: AgentDetailResponse }) {
  const { t } = useTranslation();
  const r = detail.record;
  return (
    <Row gutter={16}>
      <Col span={24}>
        <Card title={t("agent_detail.config_summary")}>
          <dl
            style={{
              display: "grid",
              gridTemplateColumns: "160px 1fr",
              rowGap: 8,
              columnGap: 16,
              margin: 0,
              fontSize: 13,
            }}
          >
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_id")}</dt>
            <dd style={{ margin: 0 }} className="mono">
              {r.id}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_tenant")}</dt>
            <dd style={{ margin: 0 }} className="mono">
              {r.tenant_id}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_spec_sha")}</dt>
            <dd style={{ margin: 0 }} className="mono">
              {r.spec_sha256}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_status")}</dt>
            <dd style={{ margin: 0 }}>{r.status}</dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_created")}</dt>
            <dd style={{ margin: 0 }}>
              {new Date(r.created_at).toLocaleString()} · {r.created_by}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_updated")}</dt>
            <dd style={{ margin: 0 }}>{new Date(r.updated_at).toLocaleString()}</dd>
          </dl>
        </Card>
      </Col>
    </Row>
  );
}
