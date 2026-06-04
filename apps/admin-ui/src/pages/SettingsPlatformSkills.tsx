/**
 * Settings — Platform Skills page (Stream X, system_admin only).
 *
 * Platform-level (NOT tenant-scoped) management of the curated reusable
 * skill catalog. system_admin only (mirrors the backend ``is_system_admin``
 * gate); non-admins see a notice. Lists platform skills with name /
 * category / required-tier badge / status badge / latest-version badge /
 * pin marker, plus a "New skill" action, a create drawer, and a per-row
 * Manage drawer for the version + lifecycle controls.
 *
 * No DELETE endpoint exists — retiring a platform skill means setting
 * ``status=archived`` from the Manage drawer.
 *
 * Mirrors ``SettingsMcpCatalog`` gating + layout (PageHeader + admin gate
 * + antd Table + ``ApiError`` → ``${code}: ${message}`` toasts).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, App, Button, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { Pin, RefreshCw, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../components/PageHeader";
import {
  listPlatformSkills,
  patchPlatformSkill,
  type PlatformSkill,
  type PlatformSkillStatus,
  type PlatformSkillTier,
} from "../api/platform-skills";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PlatformSkillCreateDrawer } from "../components/platform_skills/PlatformSkillCreateDrawer";
import { PlatformSkillManageDrawer } from "../components/platform_skills/PlatformSkillManageDrawer";

const { Text } = Typography;

const TIER_COLOR: Record<PlatformSkillTier, string> = {
  free: "default",
  pro: "blue",
  enterprise: "gold",
};

const STATUS_COLOR: Record<PlatformSkillStatus, string> = {
  draft: "default",
  active: "success",
  archived: "warning",
};

export function SettingsPlatformSkills() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const auth = useAuth();
  const isSystemAdmin = auth.identity?.isSystemAdmin ?? false;

  const [rows, setRows] = useState<PlatformSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [managing, setManaging] = useState<PlatformSkill | null>(null);

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
      const result = await listPlatformSkills();
      setRows(result.items);
      // Keep the open Manage drawer's skill in sync with the refreshed
      // list so its lifecycle controls reflect the latest server state.
      setManaging((prev) =>
        prev !== null ? result.items.find((r) => r.id === prev.id) ?? prev : prev,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isSystemAdmin) {
      void refresh();
    }
  }, [isSystemAdmin, refresh]);

  const openCreate = useCallback(() => setCreateOpen(true), []);

  const openManage = useCallback((row: PlatformSkill) => {
    setManaging(row);
    setManageOpen(true);
  }, []);

  const onPinToggle = useCallback(
    async (row: PlatformSkill) => {
      try {
        await patchPlatformSkill(row.id, { pinned: !row.pinned });
        void refresh();
      } catch (err) {
        message.error(errText(err));
      }
    },
    [errText, message, refresh],
  );

  const columns: TableColumnsType<PlatformSkill> = useMemo(
    () => [
      {
        title: t("platform_skills.col_name"),
        key: "name",
        render: (_v, row) => (
          <Space size={6}>
            {row.pinned && (
              <Tooltip title={t("platform_skills.pinned")}>
                <Pin
                  size={12}
                  strokeWidth={2}
                  style={{ color: "var(--hx-color-brand-500)" }}
                  data-testid={`ps-pin-icon-${row.id}`}
                />
              </Tooltip>
            )}
            <Text strong>{row.name}</Text>
          </Space>
        ),
      },
      {
        title: t("platform_skills.col_category"),
        dataIndex: "category",
        key: "category",
        width: 140,
        render: (category: string) =>
          category ? <Tag>{category}</Tag> : <Text type="secondary">—</Text>,
      },
      {
        title: t("platform_skills.col_tier"),
        dataIndex: "required_tier",
        key: "required_tier",
        width: 120,
        render: (tier: PlatformSkillTier) => (
          <Tag color={TIER_COLOR[tier]}>{t(`platform_skills.tier_${tier}`)}</Tag>
        ),
      },
      {
        title: t("platform_skills.col_status"),
        dataIndex: "status",
        key: "status",
        width: 120,
        render: (status: PlatformSkillStatus) => (
          <Tag color={STATUS_COLOR[status]}>{t(`platform_skills.status_${status}`)}</Tag>
        ),
      },
      {
        title: t("platform_skills.col_version"),
        dataIndex: "latest_version",
        key: "latest_version",
        width: 110,
        render: (v: number | null) =>
          v !== null ? (
            <Tag bordered={false}>v{v}</Tag>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
      {
        title: t("platform_skills.col_actions"),
        key: "actions",
        width: 200,
        render: (_v, row) => (
          <div style={{ display: "flex", gap: 6 }}>
            <Button size="small" onClick={() => openManage(row)} data-testid={`ps-manage-${row.id}`}>
              {t("platform_skills.manage")}
            </Button>
            <Button
              size="small"
              onClick={() => onPinToggle(row)}
              data-testid={`ps-pin-toggle-${row.id}`}
            >
              {row.pinned ? t("platform_skills.unpin") : t("platform_skills.pin")}
            </Button>
          </div>
        ),
      },
    ],
    [t, openManage, onPinToggle],
  );

  const emptyText = (
    <div style={{ textAlign: "center", padding: "32px 0" }} data-testid="ps-empty">
      <Sparkles size={32} strokeWidth={1.25} style={{ opacity: 0.35, marginBottom: 8 }} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{t("platform_skills.empty_title")}</div>
      <div
        style={{
          color: "var(--hx-text-tertiary, #666)",
          maxWidth: 360,
          margin: "0 auto 16px",
        }}
      >
        {t("platform_skills.empty_hint")}
      </div>
      <Button type="primary" onClick={openCreate}>
        {t("platform_skills.add")}
      </Button>
    </div>
  );

  return (
    <div data-testid="ps-root">
      <PageHeader
        icon={<Sparkles size={18} strokeWidth={1.5} />}
        title={t("platform_skills.page_title")}
        subtitle={t("platform_skills.subtitle")}
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
              <Button type="primary" onClick={openCreate} data-testid="ps-add">
                {t("platform_skills.add")}
              </Button>
            </div>
          )
        }
      />

      {!isSystemAdmin ? (
        <Alert
          type="warning"
          showIcon
          message={t("platform_skills.not_admin_title")}
          description={t("platform_skills.not_admin_body")}
          data-testid="ps-not-admin"
        />
      ) : (
        <>
          {error !== null && (
            <Alert
              type="error"
              showIcon
              message={t("platform_skills.failed_to_load")}
              description={error}
              style={{ marginBottom: 12 }}
              data-testid="ps-error"
            />
          )}
          <Table<PlatformSkill>
            columns={columns}
            dataSource={rows}
            rowKey={(r) => r.id}
            loading={loading}
            pagination={false}
            locale={{ emptyText }}
            data-testid="ps-table"
          />
        </>
      )}

      <PlatformSkillCreateDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          void refresh();
        }}
      />

      <PlatformSkillManageDrawer
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        onChanged={() => void refresh()}
        skill={managing}
      />
    </div>
  );
}
