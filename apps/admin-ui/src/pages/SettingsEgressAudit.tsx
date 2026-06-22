/**
 * Sandbox egress audit page — sandbox-egress §3.1 Phase 3.
 *
 * Operator view over ``sandbox_egress_audit``: every sandbox→internet
 * connection through the transparent egress proxy (host / port / byte volumes
 * / verdict — never payload, HTTPS is tunnelled). Filter chips (verdict / host
 * / agent), a timeline on the left, a Drawer for the full row. This is the
 * "audit over blocking" surface — egress is allowed + traced, not walled.
 *
 * Cursor pagination is opaque; cross-tenant view needs system_admin (backend
 * + SDK gate it, the UI just plumbs the current ``TenantScope``).
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Select,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Globe2,
  Network,
  ShieldAlert,
  ShieldOff,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  EGRESS_VERDICTS,
  listEgressAudit,
  type EgressAuditEntry,
  type EgressVerdict,
  type ListEgressAuditParams,
} from "../api/egressAudit";
import { PageHeader } from "../components/PageHeader";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

/** Compact byte count, e.g. ``1.2 KB`` / ``3.4 MB``. */
function formatBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)} KB`;
  return `${n} B`;
}

function VerdictTag({ verdict }: { verdict: EgressVerdict }) {
  if (verdict === "allowed") {
    return (
      <Tag color="success" icon={<CheckCircle2 size={11} strokeWidth={1.75} />}>
        allowed
      </Tag>
    );
  }
  if (verdict === "blocked_ssrf") {
    return (
      <Tag color="error" icon={<ShieldAlert size={11} strokeWidth={1.75} />}>
        blocked_ssrf
      </Tag>
    );
  }
  if (verdict === "blocked_allowlist") {
    return (
      <Tag color="warning" icon={<ShieldOff size={11} strokeWidth={1.75} />}>
        blocked_allowlist
      </Tag>
    );
  }
  if (verdict === "blocked_auth") {
    return (
      <Tag color="warning" icon={<ShieldOff size={11} strokeWidth={1.75} />}>
        blocked_auth
      </Tag>
    );
  }
  return (
    <Tag color="error" icon={<AlertTriangle size={11} strokeWidth={1.75} />}>
      upstream_error
    </Tag>
  );
}

export function SettingsEgressAudit() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { apiTenantScope } = useTenantScope();

  const [verdictFilter, setVerdictFilter] = useState<EgressVerdict | undefined>(undefined);
  const [hostFilter, setHostFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");

  const [entries, setEntries] = useState<EgressAuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [appliedScope, setAppliedScope] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EgressAuditEntry | null>(null);

  const buildParams = useCallback(
    (next: { cursor?: string | null } = {}): ListEgressAuditParams => ({
      tenantScope: apiTenantScope,
      verdict: verdictFilter,
      targetHost: hostFilter.trim().length > 0 ? hostFilter.trim() : undefined,
      agentName: agentFilter.trim().length > 0 ? agentFilter.trim() : undefined,
      cursor: next.cursor,
    }),
    [apiTenantScope, verdictFilter, hostFilter, agentFilter],
  );

  const fetchFirstPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listEgressAudit(buildParams());
      setEntries(result.items);
      setCursor(result.next_cursor);
      setHasMore(result.has_more);
      setAppliedScope(result.applied_scope);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  const loadMore = useCallback(async () => {
    if (cursor === null) return;
    setLoadingMore(true);
    try {
      const result = await listEgressAudit(buildParams({ cursor }));
      setEntries((prev) => [...prev, ...result.items]);
      setCursor(result.next_cursor);
      setHasMore(result.has_more);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoadingMore(false);
    }
  }, [buildParams, cursor, message]);

  useEffect(() => {
    fetchFirstPage();
  }, [fetchFirstPage]);

  const isCrossTenant = appliedScope === "cross_tenant";

  return (
    <div data-testid="egress-audit-root">
      <PageHeader
        icon={<Network size={18} strokeWidth={1.5} />}
        title={t("egress_audit.page_title")}
        subtitle={t("egress_audit.subtitle")}
        actions={
          isCrossTenant && (
            <Tag
              icon={<Globe2 size={12} strokeWidth={1.5} />}
              color="purple"
              data-testid="egress-audit-cross-banner"
            >
              {t("egress_audit.cross_tenant_banner")}
            </Tag>
          )
        }
      />

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          marginBottom: 16,
          padding: 12,
          background: "var(--hx-surface-raised)",
          borderRadius: 6,
          border: "1px solid var(--hx-border-subtle)",
        }}
        data-testid="egress-audit-filters"
      >
        <Select<EgressVerdict>
          placeholder={t("egress_audit.filter_verdict")}
          aria-label={t("egress_audit.filter_verdict")}
          value={verdictFilter}
          onChange={(v) => setVerdictFilter(v)}
          style={{ width: 200 }}
          allowClear
          options={EGRESS_VERDICTS.map((v) => ({ value: v, label: v }))}
          data-testid="egress-audit-verdict-filter"
        />
        <Input
          placeholder={t("egress_audit.filter_host")}
          aria-label={t("egress_audit.filter_host")}
          value={hostFilter}
          onChange={(e) => setHostFilter(e.target.value)}
          style={{ width: 220 }}
          allowClear
          data-testid="egress-audit-host-filter"
        />
        <Input
          placeholder={t("egress_audit.filter_agent")}
          aria-label={t("egress_audit.filter_agent")}
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          style={{ width: 200 }}
          allowClear
          data-testid="egress-audit-agent-filter"
        />
      </div>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("egress_audit.load_error")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="egress-audit-error"
        />
      )}

      {loading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : entries.length === 0 ? (
        <Empty description={t("egress_audit.empty")} data-testid="egress-audit-empty" />
      ) : (
        <div data-testid="egress-audit-list">
          {entries.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setSelected(entry)}
              data-testid="egress-audit-row"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                width: "100%",
                textAlign: "left",
                padding: "10px 12px",
                marginBottom: 6,
                background: "var(--hx-surface-raised)",
                border: "1px solid var(--hx-border-subtle)",
                borderRadius: 6,
                cursor: "pointer",
              }}
            >
              <VerdictTag verdict={entry.verdict} />
              <Text strong style={{ fontFamily: "var(--hx-font-mono)" }}>
                {entry.target_host}:{entry.target_port}
              </Text>
              <Text type="secondary">{entry.agent_name ?? "—"}</Text>
              <span style={{ marginLeft: "auto", display: "inline-flex", gap: 10 }}>
                <Text type="secondary">
                  <ArrowUp size={11} /> {formatBytes(entry.bytes_up)}
                </Text>
                <Text type="secondary">
                  <ArrowDown size={11} /> {formatBytes(entry.bytes_down)}
                </Text>
                <Text type="secondary">{entry.occurred_at}</Text>
              </span>
            </button>
          ))}
          {hasMore && (
            <Button
              onClick={loadMore}
              loading={loadingMore}
              block
              style={{ marginTop: 8 }}
              data-testid="egress-audit-load-more"
            >
              {t("egress_audit.load_more")}
            </Button>
          )}
        </div>
      )}

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={t("egress_audit.detail_title")}
        width={460}
        data-testid="egress-audit-drawer"
      >
        {selected !== null && (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label={t("egress_audit.col_verdict")}>
              <VerdictTag verdict={selected.verdict} />
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_host")}>
              {selected.target_host}:{selected.target_port}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_agent")}>
              {selected.agent_name ?? "—"}
              {selected.agent_version !== null ? ` @ ${selected.agent_version}` : ""}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_bytes_up")}>
              {formatBytes(selected.bytes_up)}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_bytes_down")}>
              {formatBytes(selected.bytes_down)}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_duration")}>
              {selected.duration_ms !== null ? `${selected.duration_ms} ms` : "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_sandbox")}>
              <Text style={{ fontFamily: "var(--hx-font-mono)" }}>
                {selected.sandbox_id ?? "—"}
              </Text>
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_error")}>
              {selected.error_msg ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label={t("egress_audit.col_time")}>
              {selected.occurred_at}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
