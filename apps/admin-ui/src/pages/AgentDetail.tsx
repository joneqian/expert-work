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
 * All tabs are wired: ``overview`` / ``manifest`` / ``history`` /
 * ``playground`` plus the per-agent sub-views (Runs / Skills /
 * Triggers / Memory, Stream H.6 PR 2 — backed by the agent list
 * filters from H.6 PR 1).
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
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
import { Bot, Network, ShieldOff } from "lucide-react";
import { useTranslation } from "react-i18next";

import { getAgent, type AgentDetailResponse } from "../api/agents";
import { ApiError } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { HistoryTab } from "./agent_detail/HistoryTab";
import { ManifestTab } from "./agent_detail/ManifestTab";
import { MemoryTab } from "./agent_detail/MemoryTab";
import { PlaygroundTab } from "./agent_detail/PlaygroundTab";
import { RunsTab } from "./agent_detail/RunsTab";
import { SkillsTab } from "./agent_detail/SkillsTab";
import { TriggersTab } from "./agent_detail/TriggersTab";

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
      <PageHeader
        title={record.name}
        icon={<Bot size={20} strokeWidth={1.5} />}
        backTo={{ label: t("nav.agents"), to: "/agents" }}
        subtitle={
          <Space size={12} align="center" wrap>
            <Tag color={STATUS_COLOR[record.status] ?? "default"} bordered={false}>
              {t(`agents_page.status_${record.status}`, { defaultValue: record.status })}
            </Tag>
            <Text code style={{ fontSize: 12 }}>
              v{record.version}
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.spec_sha256.slice(0, 12)}…
            </Text>
          </Space>
        }
      />

      <Tabs
        activeKey={activeTab}
        onChange={(k) =>
          nav(`/agents/${encodeURIComponent(name)}/${encodeURIComponent(version)}/${k}`)
        }
        items={[
          { key: "overview", label: t("agent_detail.tab_overview") },
          { key: "manifest", label: t("agent_detail.tab_manifest") },
          { key: "history", label: t("agent_detail.tab_history") },
          { key: "playground", label: t("agent_detail.tab_playground") },
          { key: "runs", label: t("agent_detail.tab_runs") },
          { key: "skills", label: t("agent_detail.tab_skills") },
          { key: "triggers", label: t("agent_detail.tab_triggers") },
          { key: "memory", label: t("agent_detail.tab_memory") },
        ]}
      />

      {activeTab === "overview" && <OverviewTab detail={detail} />}
      {activeTab === "manifest" && <ManifestTab detail={detail} onSaved={refresh} />}
      {activeTab === "history" && <HistoryTab detail={detail} onRolledBack={refresh} />}
      {activeTab === "playground" && <PlaygroundTab detail={detail} />}
      {activeTab === "runs" && <RunsTab detail={detail} />}
      {activeTab === "skills" && <SkillsTab detail={detail} />}
      {activeTab === "triggers" && <TriggersTab detail={detail} />}
      {activeTab === "memory" && <MemoryTab />}
      {![
        "overview",
        "manifest",
        "history",
        "playground",
        "runs",
        "skills",
        "triggers",
        "memory",
      ].includes(activeTab) && (
        <Empty
          description={t("agent_detail.tab_coming_soon", { tab: activeTab })}
          style={{ marginTop: 64 }}
          data-testid="agent-detail-tab-placeholder"
        />
      )}
    </div>
  );
}

/** Read ``sandbox.network`` out of the loosely-typed manifest spec
 *  (sandbox-egress §3.3). Egress defaults to ``"proxy"`` (manifest default). */
function readEgress(spec: Record<string, unknown>): { egress: string; allowlist: string[] } {
  const sandbox = spec.sandbox as Record<string, unknown> | undefined;
  const network = sandbox?.network as Record<string, unknown> | undefined;
  const egressRaw = network?.egress;
  const egress = typeof egressRaw === "string" ? egressRaw : "proxy";
  const allowlistRaw = network?.allowlist;
  const allowlist = Array.isArray(allowlistRaw)
    ? allowlistRaw.filter((h): h is string => typeof h === "string")
    : [];
  return { egress, allowlist };
}

function OverviewTab({ detail }: { detail: AgentDetailResponse }) {
  const { t } = useTranslation();
  const r = detail.record;
  const egress = readEgress(r.spec);
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
            <dd style={{ margin: 0 }}>
              {t(`agents_page.status_${r.status}`, { defaultValue: r.status })}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_created")}</dt>
            <dd style={{ margin: 0 }}>
              {new Date(r.created_at).toLocaleString()} · {r.created_by}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("agent_detail.field_updated")}</dt>
            <dd style={{ margin: 0 }}>{new Date(r.updated_at).toLocaleString()}</dd>
          </dl>
        </Card>
      </Col>
      <Col span={24} style={{ marginTop: 16 }}>
        <Card title={t("agent_detail.egress_title")} data-testid="agent-egress-card">
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
            <dt style={{ color: "var(--hx-text-tertiary)" }}>
              {t("agent_detail.egress_policy")}
            </dt>
            <dd style={{ margin: 0 }}>
              {egress.egress === "none" ? (
                <Tag icon={<ShieldOff size={11} strokeWidth={1.75} />}>
                  {t("agent_detail.egress_isolated")}
                </Tag>
              ) : (
                <Tag color="cyan" icon={<Network size={11} strokeWidth={1.75} />}>
                  {t("agent_detail.egress_proxied")}
                </Tag>
              )}
            </dd>
            <dt style={{ color: "var(--hx-text-tertiary)" }}>
              {t("agent_detail.egress_allowlist")}
            </dt>
            <dd style={{ margin: 0 }}>
              {egress.egress === "none" ? (
                <span style={{ color: "var(--hx-text-tertiary)" }}>—</span>
              ) : egress.allowlist.length === 0 ? (
                <span style={{ color: "var(--hx-text-tertiary)" }}>
                  {t("agent_detail.egress_allow_all")}
                </span>
              ) : (
                <Space size={[4, 4]} wrap>
                  {egress.allowlist.map((host) => (
                    <Tag key={host} className="mono">
                      {host}
                    </Tag>
                  ))}
                </Space>
              )}
            </dd>
          </dl>
        </Card>
      </Col>
    </Row>
  );
}
