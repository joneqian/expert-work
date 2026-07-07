/**
 * Settings — MCP Servers page (Stream V-F).
 *
 * Lists MCP servers registered by the current tenant (``GET /v1/mcp-servers``).
 * Table columns: 名称 / 传输 / URL / 认证 / 状态 / 工具 / 操作.
 *
 * Health probing is **on-demand** — the 状态 column shows a static
 * enabled/disabled badge on load; when the user clicks 测试 or expands a row
 * the page calls ``GET /v1/mcp-servers/{name}/tools`` and transitions the
 * per-row probe state through: idle → testing → connected(count) | unreachable.
 *
 * Mirrors the structure of SettingsTenants (PageHeader + antd Table +
 * loading/error/empty states + ``reload()`` + testids).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { Plug } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteMcpServer,
  listMcpServerTools,
  listMcpServers,
  updateMcpServer,
  type McpServer,
  type McpTool,
} from "../api/mcp-servers";
import { ApiError } from "../api/client";
import { CreateMcpServerDrawer } from "../components/CreateMcpServerDrawer";
import { AddMcpServerDrawer } from "../components/mcp_catalog/AddMcpServerDrawer";
import { PageHeader } from "../components/PageHeader";

// ── Types ──────────────────────────────────────────────────────────────────

type ProbeState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "connected"; count: number; tools: McpTool[] }
  | { kind: "unreachable" };

// ── Component ──────────────────────────────────────────────────────────────

export function SettingsMcpServers() {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [rows, setRows] = useState<McpServer[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Catalog "Add MCP server" flow (browse → instantiate / advanced custom).
  const [addOpen, setAddOpen] = useState(false);
  // Edit drawer — the legacy single-server editor, reused for edits only.
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);

  // Per-row probe state keyed by server name.
  const [probes, setProbes] = useState<Record<string, ProbeState>>({});

  // ── Data loading ───────────────────────────────────────────────────────

  const reload = useCallback(() => {
    setLoading(true);
    listMcpServers().then(
      (data) => {
        setRows(data);
        setLoading(false);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : "unknown error");
        setLoading(false);
      },
    );
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // ── Probe helper ────────────────────────────────────────────────────────

  const probe = useCallback(
    async (name: string) => {
      const current = probes[name];
      // Already connected (tools cached) or in progress — skip.
      if (current?.kind === "connected" || current?.kind === "testing") {
        return;
      }
      setProbes((prev) => ({ ...prev, [name]: { kind: "testing" } }));
      try {
        const tools = await listMcpServerTools(name);
        setProbes((prev) => ({
          ...prev,
          [name]: { kind: "connected", count: tools.length, tools },
        }));
      } catch {
        setProbes((prev) => ({ ...prev, [name]: { kind: "unreachable" } }));
      }
    },
    [probes],
  );

  // ── Actions ─────────────────────────────────────────────────────────────

  const handleToggle = useCallback(
    async (row: McpServer) => {
      try {
        await updateMcpServer(row.name, { enabled: !row.enabled });
        reload();
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : err instanceof Error
              ? err.message
              : "unknown error";
        message.error(msg);
      }
    },
    [message, reload],
  );

  const handleDelete = useCallback(
    async (name: string) => {
      try {
        await deleteMcpServer(name);
        reload();
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : err instanceof Error
              ? err.message
              : "unknown error";
        message.error(msg);
      }
    },
    [message, reload],
  );

  const openCreate = useCallback(() => {
    setAddOpen(true);
  }, []);

  const openEdit = useCallback((row: McpServer) => {
    setEditing(row);
    setEditOpen(true);
  }, []);

  const closeEdit = useCallback(() => {
    setEditOpen(false);
    setEditing(null);
  }, []);

  // ── Status badge helper ─────────────────────────────────────────────────

  const renderProbeStatus = useCallback(
    (name: string, enabled: boolean) => {
      const probe_state = probes[name] ?? { kind: "idle" };
      if (probe_state.kind === "idle") {
        return (
          <Tag color={enabled ? "green" : "default"}>
            {enabled ? t("mcp_servers.status_enabled") : t("mcp_servers.status_disabled")}
          </Tag>
        );
      }
      if (probe_state.kind === "testing") {
        return (
          <Space size={4}>
            <Spin size="small" />
            <span>{t("mcp_servers.testing")}</span>
          </Space>
        );
      }
      if (probe_state.kind === "connected") {
        return (
          <Tag color="green">
            {t("mcp_servers.connected", { count: probe_state.count })}
          </Tag>
        );
      }
      // unreachable
      return <Tag color="red">{t("mcp_servers.unreachable")}</Tag>;
    },
    [probes, t],
  );

  // ── Columns ─────────────────────────────────────────────────────────────

  const columns: ColumnsType<McpServer> = [
    {
      title: t("mcp_servers.col_name"),
      dataIndex: "name",
      key: "name",
      render: (name: string) => (
        <Typography.Text strong>{name}</Typography.Text>
      ),
    },
    {
      title: t("mcp_servers.col_transport"),
      dataIndex: "transport",
      key: "transport",
      render: (transport: string) => (
        <Tag>{transport === "streamable_http" ? "Streamable HTTP" : "SSE"}</Tag>
      ),
    },
    {
      title: t("mcp_servers.col_url"),
      dataIndex: "url",
      key: "url",
      ellipsis: true,
      render: (url: string) => (
        <Tooltip title={url}>
          <Typography.Text ellipsis style={{ maxWidth: 200 }}>
            {url}
          </Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: t("mcp_servers.col_auth"),
      dataIndex: "auth_type",
      key: "auth_type",
      render: (auth: string) => (
        <Tag color={auth === "bearer" ? "blue" : "default"}>
          {auth === "bearer" ? "Bearer" : "None"}
        </Tag>
      ),
    },
    {
      title: t("mcp_servers.col_status"),
      key: "status",
      render: (_: unknown, row: McpServer) => renderProbeStatus(row.name, row.enabled),
    },
    {
      title: t("mcp_servers.col_tools"),
      key: "tools",
      render: (_: unknown, row: McpServer) => {
        const probe_state = probes[row.name];
        if (probe_state?.kind === "connected") {
          return <span>{probe_state.count}</span>;
        }
        return <span style={{ color: "var(--ew-text-tertiary, #666)" }}>—</span>;
      },
    },
    {
      title: t("mcp_servers.col_actions"),
      key: "actions",
      render: (_: unknown, row: McpServer) => (
        <Space size={4}>
          <Button
            size="small"
            data-testid={`ms-test-${row.name}`}
            loading={probes[row.name]?.kind === "testing"}
            onClick={() => void probe(row.name)}
          >
            {t("mcp_servers.test")}
          </Button>
          <Button
            size="small"
            data-testid={`ms-edit-${row.name}`}
            onClick={() => openEdit(row)}
          >
            {t("mcp_servers.edit")}
          </Button>
          <Button
            size="small"
            data-testid={`ms-toggle-${row.name}`}
            onClick={() => void handleToggle(row)}
          >
            {row.enabled ? t("mcp_servers.status_disabled") : t("mcp_servers.status_enabled")}
          </Button>
          <Popconfirm
            title={t("mcp_servers.delete_confirm", { name: row.name })}
            onConfirm={() => void handleDelete(row.name)}
          >
            <Button
              size="small"
              danger
              data-testid={`ms-delete-${row.name}`}
            >
              {t("mcp_servers.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // ── Expandable row renderer ─────────────────────────────────────────────

  const expandedRowRender = useCallback(
    (row: McpServer) => {
      const probe_state = probes[row.name] ?? { kind: "idle" };
      if (probe_state.kind === "idle" || probe_state.kind === "testing") {
        return (
          <div style={{ padding: "8px 0" }}>
            <Space size={4}>
              <Spin size="small" />
              <span>{t("mcp_servers.tools_loading")}</span>
            </Space>
          </div>
        );
      }
      if (probe_state.kind === "unreachable") {
        return (
          <div style={{ padding: "8px 0" }}>
            <Tag color="red">{t("mcp_servers.unreachable")}</Tag>
          </div>
        );
      }
      // connected
      const tools = probe_state.tools;
      if (tools.length === 0) {
        return (
          <div
            style={{ padding: "8px 0", color: "var(--ew-text-tertiary, #666)" }}
            data-testid={`ms-tools-${row.name}`}
          >
            {t("mcp_servers.no_tools")}
          </div>
        );
      }
      return (
        <div style={{ padding: "8px 0" }} data-testid={`ms-tools-${row.name}`}>
          <Space size={[4, 8]} wrap>
            {tools.map((tool) => (
              <Tooltip key={tool.name} title={tool.description || undefined}>
                <Tag style={{ cursor: "default" }}>{tool.name}</Tag>
              </Tooltip>
            ))}
          </Space>
        </div>
      );
    },
    [probes, t],
  );

  // ── Empty state ─────────────────────────────────────────────────────────

  const emptyText = (
    <div
      style={{ textAlign: "center", padding: "32px 0" }}
      data-testid="ms-empty"
    >
      <Plug size={32} strokeWidth={1.25} style={{ opacity: 0.35, marginBottom: 8 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        {t("mcp_servers.empty_title")}
      </div>
      <div
        style={{
          color: "var(--ew-text-tertiary, #666)",
          marginBottom: 16,
          maxWidth: 360,
          margin: "0 auto 16px",
        }}
      >
        {t("mcp_servers.empty_hint")}
      </div>
      <Button type="primary" onClick={openCreate}>
        {t("mcp_servers.add")}
      </Button>
    </div>
  );

  // ── Render ──────────────────────────────────────────────────────────────

  return (
    <div data-testid="ms-root">
      <PageHeader
        icon={<Plug size={18} strokeWidth={1.5} />}
        title={t("mcp_servers.page_title")}
        subtitle={t("mcp_servers.subtitle")}
        actions={
          <Button
            type="primary"
            data-testid="ms-add"
            onClick={openCreate}
          >
            {t("mcp_servers.add")}
          </Button>
        }
      />

      {error !== null && (
        <Alert
          type="error"
          showIcon
          data-testid="ms-error"
          message={t("mcp_servers.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}

      <Table<McpServer>
        data-testid="ms-table"
        rowKey="name"
        loading={loading}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText }}
        columns={columns}
        expandable={{
          expandedRowRender,
          onExpand: (expanded, row) => {
            if (expanded) {
              void probe(row.name);
            }
          },
        }}
      />

      <AddMcpServerDrawer
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSaved={() => {
          setAddOpen(false);
          reload();
        }}
      />

      <CreateMcpServerDrawer
        open={editOpen}
        onClose={closeEdit}
        onSaved={() => {
          closeEdit();
          reload();
        }}
        editing={editing}
      />
    </div>
  );
}
