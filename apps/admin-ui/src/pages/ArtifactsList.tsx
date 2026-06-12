/**
 * Artifacts page — Stream H.8 PR 1 (design § 6.8).
 *
 * Run-artifact governance surface over ``/v1/artifacts`` (Mini-ADR
 * J-25). Two honest modes (Mini-ADR H-14):
 *
 *   - home tenant — "my artifacts": full actions (download / versions /
 *     re-classify kind / soft-delete). The backend resolves the caller's
 *     user for every action and hides other users' rows behind 404, so
 *     this is by contract the caller's own slice.
 *   - cross-tenant ``"*"`` (system_admin) — read-only aggregate across
 *     every tenant/user. No per-user context server-side → no row
 *     actions; tenant/user columns appear instead.
 *
 * Per-tenant admin management of OTHER users' artifacts is a backend
 * capability change tracked as H.8-F1 — not silently faked here.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Download, Globe2, History, Package, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteArtifact,
  downloadArtifact,
  listArtifacts,
  listArtifactVersions,
  patchArtifactKind,
  type ArtifactKind,
  type ArtifactList,
  type ArtifactListItem,
  type ArtifactVersion,
} from "../api/artifacts";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { PageHeader } from "../components/PageHeader";

const { Text } = Typography;

const KIND_OPTIONS: ArtifactKind[] = ["document", "code", "data", "other"];

const KIND_COLOR: Record<ArtifactKind, string> = {
  document: "blue",
  code: "purple",
  data: "cyan",
  other: "default",
};

function errMessage(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}

export function ArtifactsList() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope, apiTenantScope } = useTenantScope();
  const [data, setData] = useState<ArtifactList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [versions, setVersions] = useState<ArtifactVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listArtifacts({ tenantScope: apiTenantScope });
      setData(result);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setLoading(false);
    }
  }, [apiTenantScope]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const isCrossTenant = data?.cross_tenant ?? scope === "*";

  const handleDownload = useCallback(
    async (name: string) => {
      setBusyName(name);
      try {
        await downloadArtifact(name);
      } catch (err) {
        message.error(t("artifacts_page.download_failed", { detail: errMessage(err) }));
      } finally {
        setBusyName(null);
      }
    },
    [t, message],
  );

  const handleDelete = useCallback(
    async (name: string) => {
      setBusyName(name);
      try {
        await deleteArtifact(name);
        message.success(t("artifacts_page.deleted", { name }));
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        setBusyName(null);
      }
    },
    [t, message, refresh],
  );

  const handleKindChange = useCallback(
    async (record: ArtifactListItem, kind: ArtifactKind) => {
      // Mini-ADR H-16 — the backend 409s a no-op; skip it client-side.
      if (kind === record.kind) return;
      setBusyName(record.name);
      try {
        await patchArtifactKind(record.name, kind);
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        setBusyName(null);
      }
    },
    [message, refresh],
  );

  const openVersions = useCallback(async (name: string) => {
    setVersionsFor(name);
    setVersionsLoading(true);
    try {
      const result = await listArtifactVersions(name);
      setVersions(result.versions);
    } catch (err) {
      message.error(errMessage(err));
      setVersions([]);
    } finally {
      setVersionsLoading(false);
    }
  }, [message]);

  const columns: TableColumnsType<ArtifactListItem> = useMemo(() => {
    const base: TableColumnsType<ArtifactListItem> = [
      {
        title: t("artifacts_page.col_name"),
        dataIndex: "name",
        key: "name",
        render: (name: string) => <Text strong>{name}</Text>,
      },
      {
        title: t("artifacts_page.col_kind"),
        dataIndex: "kind",
        key: "kind",
        width: 160,
        render: (kind: ArtifactKind, record) =>
          isCrossTenant ? (
            <Tag color={KIND_COLOR[kind]} bordered={false}>
              {kind}
            </Tag>
          ) : (
            <Select<ArtifactKind>
              value={kind}
              size="small"
              style={{ width: 130 }}
              aria-label={t("artifacts_page.col_kind")}
              disabled={busyName === record.name}
              onChange={(next) => void handleKindChange(record, next)}
              options={KIND_OPTIONS.map((k) => ({ value: k, label: k }))}
              data-testid={`artifact-kind-${record.name}`}
            />
          ),
      },
      {
        title: t("artifacts_page.col_latest"),
        dataIndex: "latest_version",
        key: "latest_version",
        width: 100,
        render: (v: number) => <Text className="mono">v{v}</Text>,
      },
    ];

    if (isCrossTenant) {
      base.push(
        {
          title: t("artifacts_page.col_tenant"),
          dataIndex: "tenant_id",
          key: "tenant_id",
          width: 140,
          render: (id?: string) =>
            id ? (
              <Tooltip title={id}>
                <Text code style={{ fontSize: 12 }}>
                  {id.slice(0, 8)}…
                </Text>
              </Tooltip>
            ) : (
              <Text type="secondary">—</Text>
            ),
        },
        {
          title: t("artifacts_page.col_user"),
          dataIndex: "user_id",
          key: "user_id",
          width: 140,
          render: (id?: string) =>
            id ? (
              <Tooltip title={id}>
                <Text code style={{ fontSize: 12 }}>
                  {id.slice(0, 8)}…
                </Text>
              </Tooltip>
            ) : (
              <Text type="secondary">—</Text>
            ),
        },
      );
    } else {
      base.push({
        title: "",
        key: "actions",
        width: 230,
        render: (_: unknown, record) => (
          <Space size={6}>
            <Button
              size="small"
              icon={<Download size={13} strokeWidth={1.5} />}
              loading={busyName === record.name}
              onClick={() => void handleDownload(record.name)}
              data-testid={`artifact-download-${record.name}`}
            >
              {t("artifacts_page.download")}
            </Button>
            <Button
              size="small"
              icon={<History size={13} strokeWidth={1.5} />}
              onClick={() => void openVersions(record.name)}
              data-testid={`artifact-versions-${record.name}`}
            >
              {t("artifacts_page.versions")}
            </Button>
            <Popconfirm
              title={t("artifacts_page.delete_confirm_title", { name: record.name })}
              description={t("artifacts_page.delete_confirm_body")}
              onConfirm={() => void handleDelete(record.name)}
              okText={t("artifacts_page.delete")}
              okButtonProps={{ danger: true }}
            >
              <Button
                size="small"
                danger
                icon={<Trash2 size={13} strokeWidth={1.5} />}
                loading={busyName === record.name}
                data-testid={`artifact-delete-${record.name}`}
              >
                {t("artifacts_page.delete")}
              </Button>
            </Popconfirm>
          </Space>
        ),
      });
    }
    return base;
  }, [t, isCrossTenant, busyName, handleDownload, handleDelete, handleKindChange, openVersions]);

  const versionColumns: TableColumnsType<ArtifactVersion> = useMemo(
    () => [
      {
        title: t("artifacts_page.ver_col_version"),
        dataIndex: "version",
        width: 90,
        render: (v: number) => <Text className="mono">v{v}</Text>,
      },
      {
        title: t("artifacts_page.ver_col_path"),
        dataIndex: "path_in_workspace",
        ellipsis: true,
        render: (p: string) => (
          <Text code style={{ fontSize: 12 }}>
            {p}
          </Text>
        ),
      },
      {
        title: t("artifacts_page.ver_col_size"),
        dataIndex: "size_bytes",
        width: 110,
        render: (size: number | null) =>
          size === null ? (
            <Tooltip title={t("artifacts_page.digest_pending")}>
              <Text type="secondary">—</Text>
            </Tooltip>
          ) : (
            <Text className="mono">{size}</Text>
          ),
      },
      {
        title: "SHA-256",
        dataIndex: "sha256",
        width: 140,
        render: (sha: string | null) =>
          sha === null ? (
            <Tooltip title={t("artifacts_page.digest_pending")}>
              <Text type="secondary">—</Text>
            </Tooltip>
          ) : (
            <Tooltip title={sha}>
              <Text code style={{ fontSize: 12 }}>
                {sha.slice(0, 12)}…
              </Text>
            </Tooltip>
          ),
      },
      {
        title: t("artifacts_page.ver_col_created"),
        dataIndex: "created_at",
        width: 190,
        render: (iso: string | null) =>
          iso ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {new Date(iso).toLocaleString()}
            </Text>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
    ],
    [t],
  );

  return (
    <div>
      <PageHeader
        icon={<Package size={18} strokeWidth={1.5} />}
        title={t("artifacts_page.page_title")}
        subtitle={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {isCrossTenant
              ? t("artifacts_page.subtitle_cross")
              : t("artifacts_page.subtitle_home")}
          </Text>
        }
        actions={
          <>
            {isCrossTenant && (
              <Tag
                icon={<Globe2 size={12} strokeWidth={1.5} />}
                color="purple"
                data-testid="cross-tenant-banner"
              >
                {t("artifacts_page.cross_tenant_banner")}
              </Tag>
            )}
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading}
              aria-label={t("common.refresh")}
              data-testid="artifacts-refresh"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                border: "1px solid var(--hx-border-default)",
                borderRadius: 6,
                background: "var(--hx-surface-raised)",
                color: "var(--hx-text-primary)",
                fontSize: 13,
                cursor: loading ? "wait" : "pointer",
              }}
            >
              <RefreshCw size={14} strokeWidth={1.5} />
              {loading ? t("common.loading") : t("common.refresh")}
            </button>
          </>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("artifacts_page.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="artifacts-error"
        />
      )}

      <Table<ArtifactListItem>
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey={(record) =>
          record.user_id ? `${record.tenant_id}/${record.user_id}/${record.name}` : record.name
        }
        loading={loading}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              description={
                isCrossTenant
                  ? t("artifacts_page.empty_cross")
                  : t("artifacts_page.empty_home")
              }
            />
          ),
        }}
        data-testid="artifacts-table"
      />

      <Drawer
        title={t("artifacts_page.versions_title", { name: versionsFor ?? "" })}
        open={versionsFor !== null}
        onClose={() => setVersionsFor(null)}
        width={720}
        data-testid="artifact-versions-drawer"
      >
        <Table<ArtifactVersion>
          size="small"
          columns={versionColumns}
          dataSource={versions}
          rowKey="version"
          loading={versionsLoading}
          pagination={false}
          locale={{ emptyText: <Empty description={t("artifacts_page.versions_empty")} /> }}
          data-testid="artifact-versions-table"
        />
      </Drawer>
    </div>
  );
}
