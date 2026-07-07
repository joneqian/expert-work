/**
 * Settings — Billing Chargeback page (Stream Z3, system_admin only).
 *
 * The ONE place the full cost split (base / markup / billed / margin) is
 * shown — a platform-admin cross-tenant view. Mirrors ``SettingsMcpCatalog``
 * gating: non-admins see a notice (testid ``chargeback-not-admin``) and the
 * fetch only runs when ``isSystemAdmin``.
 *
 * The tenant-facing usage page (``SettingsUsage``) deliberately never carries
 * base/markup/margin — that separation is the monetization no-leak rule.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  DatePicker,
  Input,
  Skeleton,
  Statistic,
  Table,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { Receipt } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/AuthContext";
import {
  getChargeback,
  type Chargeback,
  type ChargebackAgentRow,
  type ChargebackTenantRow,
} from "../api/billing-admin";
import { formatMicros } from "../utils/money";
import { cacheHitRate, formatHitRate, type CacheTokenCounts } from "../utils/cache";

const { Text } = Typography;

const MONTH_FMT = "YYYY-MM";

/** Stream RT-3 (RT-ADR-14) — cache read/write token columns + client-derived
 *  hit rate, shared by the tenant table and the per-agent drill-down (both
 *  rows extend ``TokenCounts``). System_admin already sees the full cost split
 *  here, so surfacing cache tokens carries no monetization-leak concern. */
function cacheColumns<T extends CacheTokenCounts>(
  t: (key: string) => string,
): TableColumnsType<T> {
  return [
    {
      title: t("chargeback.col_cache_read_tokens"),
      dataIndex: "cache_read_tokens",
      key: "cache_read_tokens",
      width: 140,
      align: "right",
      // ``?? 0`` — cache columns (migration 0036) may be absent on older
      // pre-metering rollup rows; never blank the row on a missing count.
      render: (v: number) => (v ?? 0).toLocaleString(),
    },
    {
      title: t("chargeback.col_cache_creation_tokens"),
      dataIndex: "cache_creation_tokens",
      key: "cache_creation_tokens",
      width: 150,
      align: "right",
      render: (v: number) => (v ?? 0).toLocaleString(),
    },
    {
      title: t("chargeback.col_hit_rate"),
      key: "hit_rate",
      width: 110,
      align: "right",
      render: (_: unknown, row: T) => formatHitRate(cacheHitRate(row)),
    },
  ];
}

function errText(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}

export function SettingsBillingChargeback() {
  const { t } = useTranslation();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [month, setMonth] = useState<Dayjs>(() => dayjs());
  const [tenantFilter, setTenantFilter] = useState("");
  const [data, setData] = useState<Chargeback | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Stream 12.4 — per-tenant agent drill-down, fetched lazily on row expand and
  // cached by tenant_id so re-expanding never refetches.
  const [agentsByTenant, setAgentsByTenant] = useState<
    Record<string, ChargebackAgentRow[]>
  >({});
  const [agentLoading, setAgentLoading] = useState<Record<string, boolean>>({});

  // Fetch the whole month's chargeback (all tenants); the ``tenantFilter`` is
  // applied client-side below. This avoids one API call per keystroke (a UUID
  // typed char-by-char would otherwise fire many requests, most with an invalid
  // partial UUID → backend 422).
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    // A new month invalidates the per-tenant agent drill-down cache.
    setAgentsByTenant({});
    setAgentLoading({});
    try {
      setData(await getChargeback({ month: month.format(MONTH_FMT) }));
    } catch (err) {
      setError(errText(err));
    } finally {
      setLoading(false);
    }
  }, [month]);

  // Lazily load one tenant's per-agent split when its row is expanded.
  const loadAgents = useCallback(
    async (tenantId: string) => {
      if (agentsByTenant[tenantId] != null || agentLoading[tenantId]) {
        return;
      }
      setAgentLoading((prev) => ({ ...prev, [tenantId]: true }));
      try {
        const detail = await getChargeback({
          month: month.format(MONTH_FMT),
          tenantId,
        });
        setAgentsByTenant((prev) => ({
          ...prev,
          [tenantId]: detail.agents ?? [],
        }));
      } catch (err) {
        setError(errText(err));
      } finally {
        setAgentLoading((prev) => ({ ...prev, [tenantId]: false }));
      }
    },
    [month, agentsByTenant, agentLoading],
  );

  useEffect(() => {
    if (isSystemAdmin) {
      void refresh();
    }
  }, [isSystemAdmin, refresh]);

  // Client-side tenant filter (substring on tenant_id) — no refetch per keystroke.
  const filteredTenants = useMemo(() => {
    const needle = tenantFilter.trim().toLowerCase();
    const rows = data?.tenants ?? [];
    if (needle.length === 0) {
      return rows;
    }
    return rows.filter((r) => r.tenant_id.toLowerCase().includes(needle));
  }, [data, tenantFilter]);

  const moneyCol = useCallback(
    (v: number) => (
      <Text style={{ fontFamily: "var(--ew-font-mono)" }}>
        {formatMicros(v)}
      </Text>
    ),
    [],
  );

  const columns: TableColumnsType<ChargebackTenantRow> = useMemo(
    () => [
      {
        title: t("chargeback.col_tenant"),
        dataIndex: "tenant_id",
        key: "tenant_id",
        render: (id: string) => (
          <Text code style={{ fontSize: 12 }}>
            {id}
          </Text>
        ),
      },
      {
        title: t("chargeback.col_input_tokens"),
        dataIndex: "input_tokens",
        key: "input_tokens",
        width: 130,
        align: "right",
        render: (v: number) => v.toLocaleString(),
      },
      {
        title: t("chargeback.col_output_tokens"),
        dataIndex: "output_tokens",
        key: "output_tokens",
        width: 130,
        align: "right",
        render: (v: number) => v.toLocaleString(),
      },
      ...cacheColumns<ChargebackTenantRow>(t),
      {
        title: t("chargeback.col_base"),
        dataIndex: "base_cost_micros",
        key: "base_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_markup"),
        dataIndex: "markup_cost_micros",
        key: "markup_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_billed"),
        dataIndex: "billed_cost_micros",
        key: "billed_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_margin"),
        dataIndex: "margin_micros",
        key: "margin_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_unpriced"),
        dataIndex: "unpriced_buckets",
        key: "unpriced_buckets",
        width: 110,
        align: "right",
        render: (v: number) => v.toLocaleString(),
      },
    ],
    [t, moneyCol],
  );

  // Per-agent columns for the drill-down inner table (Stream 12.4).
  const agentColumns: TableColumnsType<ChargebackAgentRow> = useMemo(
    () => [
      {
        title: t("chargeback.col_agent"),
        dataIndex: "agent_name",
        key: "agent_name",
        render: (name: string) => <Text strong>{name}</Text>,
      },
      {
        title: t("chargeback.col_input_tokens"),
        dataIndex: "input_tokens",
        key: "input_tokens",
        width: 130,
        align: "right",
        render: (v: number) => v.toLocaleString(),
      },
      {
        title: t("chargeback.col_output_tokens"),
        dataIndex: "output_tokens",
        key: "output_tokens",
        width: 130,
        align: "right",
        render: (v: number) => v.toLocaleString(),
      },
      ...cacheColumns<ChargebackAgentRow>(t),
      {
        title: t("chargeback.col_base"),
        dataIndex: "base_cost_micros",
        key: "base_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_markup"),
        dataIndex: "markup_cost_micros",
        key: "markup_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_billed"),
        dataIndex: "billed_cost_micros",
        key: "billed_cost_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
      {
        title: t("chargeback.col_margin"),
        dataIndex: "margin_micros",
        key: "margin_micros",
        width: 130,
        align: "right",
        render: moneyCol,
      },
    ],
    [t, moneyCol],
  );

  const renderAgents = useCallback(
    (row: ChargebackTenantRow) => {
      const agents = agentsByTenant[row.tenant_id];
      if (agents == null) {
        return <Skeleton active paragraph={{ rows: 2 }} />;
      }
      return (
        <Table<ChargebackAgentRow>
          columns={agentColumns}
          dataSource={agents}
          rowKey={(a) => a.agent_name}
          pagination={false}
          size="small"
          locale={{ emptyText: t("chargeback.agents_empty") }}
          scroll={{ x: "max-content" }}
          data-testid={`chargeback-agents-${row.tenant_id}`}
        />
      );
    },
    [agentsByTenant, agentColumns, t],
  );

  return (
    <div data-testid="chargeback-root">
      <PageHeader
        icon={<Receipt size={18} strokeWidth={1.5} />}
        title={t("chargeback.page_title")}
        subtitle={t("chargeback.subtitle")}
        actions={
          isSystemAdmin && (
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Input
                placeholder={t("chargeback.tenant_filter")}
                value={tenantFilter}
                onChange={(e) => setTenantFilter(e.target.value)}
                allowClear
                style={{ width: 280 }}
                data-testid="chargeback-tenant-filter"
              />
              <DatePicker
                picker="month"
                value={month}
                allowClear={false}
                onChange={(value) => value && setMonth(value)}
                data-testid="chargeback-month"
              />
            </div>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("chargeback.not_admin_title")}
          description={t("chargeback.not_admin_body")}
          data-testid="chargeback-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("chargeback.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="chargeback-error"
            />
          )}

          {loading && data === null ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  gap: 24,
                  flexWrap: "wrap",
                  padding: 16,
                  marginBottom: 16,
                  background: "var(--ew-surface-raised)",
                  border: "1px solid var(--ew-border-subtle)",
                  borderRadius: 6,
                }}
                data-testid="chargeback-summary"
              >
                <Statistic
                  title={t("chargeback.total_base")}
                  value={formatMicros(data?.total_base_cost_micros ?? 0)}
                  valueStyle={{ fontFamily: "var(--ew-font-mono)" }}
                />
                <Statistic
                  title={t("chargeback.total_billed")}
                  value={formatMicros(data?.total_billed_cost_micros ?? 0)}
                  valueStyle={{ fontFamily: "var(--ew-font-mono)" }}
                />
                <Statistic
                  title={t("chargeback.total_margin")}
                  value={formatMicros(data?.total_margin_micros ?? 0)}
                  valueStyle={{ fontFamily: "var(--ew-font-mono)" }}
                />
                {data?.as_of != null && (
                  <Statistic
                    title={t("chargeback.as_of")}
                    value={new Date(data.as_of).toLocaleString()}
                    valueStyle={{ fontSize: 14 }}
                  />
                )}
              </div>

              <Table<ChargebackTenantRow>
                columns={columns}
                dataSource={filteredTenants}
                rowKey={(r) => r.tenant_id}
                loading={loading}
                pagination={false}
                locale={{ emptyText: t("chargeback.empty") }}
                scroll={{ x: "max-content" }}
                data-testid="chargeback-table"
                expandable={{
                  expandedRowRender: renderAgents,
                  rowExpandable: (r) => r.unpriced_buckets >= 0,
                  onExpand: (expanded, r) => {
                    if (expanded) {
                      void loadAgents(r.tenant_id);
                    }
                  },
                }}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
