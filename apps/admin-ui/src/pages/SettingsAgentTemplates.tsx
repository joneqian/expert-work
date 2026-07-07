/**
 * Settings — Platform Agent Templates page (Stream Agent-Templates, system_admin).
 *
 * Platform-level (NOT tenant-scoped) management of the curated Agent template
 * catalog. system_admin only (mirrors the backend ``is_system_admin`` gate);
 * non-admins see a notice. Lists templates with display_name / version /
 * category / required-tier / status / enabled, plus a "New template" action and
 * a create Modal; row edit opens the detail page. Mirrors ``SettingsMcpCatalog``.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, App, Button, Popconfirm, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Bot, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import { ApiError } from "../api/client";
import {
  deleteAgentTemplate,
  listAgentTemplates,
  templateCategoryLabelKey,
  type AgentTemplate,
  type TemplateTier,
} from "../api/agent-templates";
import { useAuth } from "../auth/AuthContext";
import { AgentTemplateCreateModal } from "../components/agent_templates/AgentTemplateCreateModal";

const { Text } = Typography;

const TIER_COLOR: Record<TemplateTier, string> = {
  free: "default",
  pro: "blue",
  enterprise: "gold",
};

export function SettingsAgentTemplates() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;
  const navigate = useNavigate();

  const [rows, setRows] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const errText = useCallback(
    (err: unknown): string =>
      err instanceof ApiError
        ? `${err.code}: ${err.message}`
        : err instanceof Error
          ? err.message
          : "unknown error",
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listAgentTemplates());
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isSystemAdmin) void refresh();
  }, [isSystemAdmin, refresh]);

  const openEdit = useCallback(
    (row: AgentTemplate) => {
      navigate(
        `/settings/agent-templates/${encodeURIComponent(row.name)}/${encodeURIComponent(row.version)}`,
      );
    },
    [navigate],
  );

  const onDelete = useCallback(
    async (row: AgentTemplate) => {
      try {
        await deleteAgentTemplate(row.name, row.version);
        message.success(t("agent_templates.deleted"));
        void refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh, t],
  );

  const columns: TableColumnsType<AgentTemplate> = useMemo(
    () => [
      {
        title: t("agent_templates.col_name"),
        key: "name",
        render: (_v, row) => (
          <div>
            <Text strong>{row.display_name}</Text>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {row.name}@{row.version}
              </Text>
            </div>
          </div>
        ),
      },
      {
        title: t("agent_templates.col_category"),
        dataIndex: "category",
        key: "category",
        width: 140,
        render: (category: string) => {
          if (!category) return <Text type="secondary">—</Text>;
          const key = templateCategoryLabelKey(category);
          return <Tag>{key ? t(key) : category}</Tag>;
        },
      },
      {
        title: t("agent_templates.col_tier"),
        dataIndex: "required_tier",
        key: "required_tier",
        width: 110,
        render: (tier: TemplateTier) => (
          <Tag color={TIER_COLOR[tier]}>{t(`agent_templates.tier_${tier}`)}</Tag>
        ),
      },
      {
        title: t("agent_templates.col_status"),
        dataIndex: "status",
        key: "status",
        width: 110,
        render: (status: string) => (
          <Tag color={status === "published" ? "green" : "default"}>
            {t(`agent_templates.status_${status}`)}
          </Tag>
        ),
      },
      {
        title: t("agent_templates.col_enabled"),
        dataIndex: "enabled",
        key: "enabled",
        width: 90,
        render: (enabled: boolean) =>
          enabled ? <Tag color="green">{t("agent_templates.yes")}</Tag> : <Tag>{t("agent_templates.no")}</Tag>,
      },
      {
        title: t("agent_templates.col_actions"),
        key: "actions",
        width: 170,
        render: (_v, row) => (
          <div style={{ display: "flex", gap: 6 }}>
            <Button
              size="small"
              onClick={() => openEdit(row)}
              data-testid={`at-edit-${row.name}`}
            >
              {t("common.edit")}
            </Button>
            <Popconfirm
              title={t("agent_templates.delete_confirm", { name: row.display_name })}
              okType="danger"
              okText={t("common.delete")}
              cancelText={t("common.cancel")}
              onConfirm={() => onDelete(row)}
            >
              <Button size="small" danger data-testid={`at-delete-${row.name}`}>
                {t("common.delete")}
              </Button>
            </Popconfirm>
          </div>
        ),
      },
    ],
    [t, openEdit, onDelete],
  );

  const emptyText = (
    <div style={{ textAlign: "center", padding: "32px 0" }} data-testid="at-empty">
      <Bot size={32} strokeWidth={1.25} style={{ opacity: 0.35, marginBottom: 8 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t("agent_templates.empty_title")}</div>
      <div style={{ color: "var(--ew-text-tertiary, #666)", maxWidth: 360, margin: "0 auto 16px" }}>
        {t("agent_templates.empty_hint")}
      </div>
      <Button type="primary" onClick={() => setCreateOpen(true)}>
        {t("agent_templates.add")}
      </Button>
    </div>
  );

  return (
    <div data-testid="at-root">
      <PageHeader
        icon={<Bot size={18} strokeWidth={1.5} />}
        title={t("agent_templates.page_title")}
        subtitle={t("agent_templates.subtitle")}
        actions={
          isSystemAdmin && (
            <div style={{ display: "flex", gap: 8 }}>
              <Button
                onClick={() => void refresh()}
                loading={loading}
                icon={<RefreshCw size={14} strokeWidth={1.5} />}
              >
                {t("common.refresh")}
              </Button>
              <Button type="primary" onClick={() => setCreateOpen(true)} data-testid="at-add">
                {t("agent_templates.add")}
              </Button>
            </div>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("agent_templates.not_admin_title")}
          description={t("agent_templates.not_admin_body")}
          data-testid="at-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("agent_templates.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="at-error"
            />
          )}
          <Table<AgentTemplate>
            columns={columns}
            dataSource={rows}
            rowKey={(r) => r.id}
            loading={loading}
            pagination={false}
            locale={{ emptyText }}
            data-testid="at-table"
          />
        </>
      )}

      <AgentTemplateCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onSaved={() => void refresh()}
      />
    </div>
  );
}
