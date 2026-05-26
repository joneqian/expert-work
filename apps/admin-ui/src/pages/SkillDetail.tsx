/**
 * Skill detail page — Stream H.4 PR 5.
 *
 * Hero (name + status + category) + Metadata card + Version table with
 * a per-row Export-ZIP action. Status change goes through PATCH
 * (``draft → active → archived`` lifecycle).
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Card,
  Empty,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ChevronRight, Download, FileCode2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import {
  exportSkillVersion,
  getSkill,
  listSkillVersions,
  patchSkillStatus,
  type SkillRecord,
  type SkillStatus,
  type SkillVersion,
} from "../api/skills";
import { ApiError } from "../api/client";

const { Text } = Typography;

const STATUS_OPTIONS: SkillStatus[] = ["draft", "active", "archived"];

const STATUS_COLOR: Record<SkillStatus, string> = {
  draft: "default",
  active: "success",
  archived: "warning",
};

export function SkillDetail() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { skillId } = useParams<{ skillId: string }>();

  const [skill, setSkill] = useState<SkillRecord | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusSubmitting, setStatusSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    if (!skillId) return;
    setLoading(true);
    setError(null);
    try {
      const [skillResult, versionsResult] = await Promise.all([
        getSkill(skillId),
        listSkillVersions(skillId),
      ]);
      setSkill(skillResult);
      setVersions(versionsResult.items);
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
  }, [skillId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onChangeStatus = useCallback(
    async (next: SkillStatus) => {
      if (skill === null) return;
      setStatusSubmitting(true);
      try {
        const updated = await patchSkillStatus(skill.id, { status: next });
        setSkill(updated);
        message.success(t("skills.status_changed", { status: next }));
      } catch (err) {
        message.error(err instanceof Error ? err.message : "failed");
      } finally {
        setStatusSubmitting(false);
      }
    },
    [skill, message, t],
  );

  const onExport = useCallback(
    async (version: SkillVersion) => {
      if (skill === null) return;
      try {
        const blob = await exportSkillVersion(skill.id, version.version);
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${skill.name}-v${version.version}.skill.zip`;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        URL.revokeObjectURL(url);
      } catch (err) {
        message.error(err instanceof Error ? err.message : "export failed");
      }
    },
    [skill, message],
  );

  if (loading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  if (error !== null || skill === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("skills.failed_to_load")}
        description={error ?? "skill not found"}
        data-testid="skill-detail-error"
      />
    );
  }

  const versionColumns: TableColumnsType<SkillVersion> = [
    {
      title: t("skills.col_version"),
      dataIndex: "version",
      key: "version",
      width: 100,
      render: (v: number) => <Tag bordered={false}>v{v}</Tag>,
    },
    {
      title: t("skills.col_tools"),
      dataIndex: "tool_names",
      key: "tools",
      render: (tools: string[]) =>
        tools.length === 0 ? (
          <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        ) : (
          <Space size={4} wrap>
            {tools.map((tool) => (
              <Tag key={tool} bordered={false}>{tool}</Tag>
            ))}
          </Space>
        ),
    },
    {
      title: t("skills.col_authored_by"),
      dataIndex: "authored_by",
      key: "authored_by",
      width: 140,
    },
    {
      title: t("skills.col_created"),
      dataIndex: "created_at",
      key: "created_at",
      width: 180,
      render: (iso: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{new Date(iso).toLocaleString()}</Text>
      ),
    },
    {
      title: t("skills.col_actions"),
      key: "actions",
      width: 140,
      render: (_, record) => (
        <Button
          size="small"
          icon={<Download size={12} strokeWidth={1.75} />}
          onClick={() => onExport(record)}
          data-testid={`skill-export-${record.version}`}
        >
          {t("skills.export_zip")}
        </Button>
      ),
    },
  ];

  return (
    <div data-testid="skill-detail-root">
      <Breadcrumb
        separator={<ChevronRight size={12} strokeWidth={1.5} />}
        items={[
          { title: <Link to="/skills">{t("skills.page_title")}</Link> },
          { title: <Text code style={{ fontSize: 12 }}>{skill.name}</Text> },
        ]}
        style={{ marginBottom: 8 }}
      />

      <div className="hx-page-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <FileCode2 size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0, fontFamily: "var(--hx-font-mono)" }}>{skill.name}</h1>
          <Tag color={STATUS_COLOR[skill.status]}>{skill.status}</Tag>
          {skill.latest_version !== null && (
            <Tooltip title={t("skills.latest_version_hint")}>
              <Tag bordered={false}>v{skill.latest_version}</Tag>
            </Tooltip>
          )}
          <span style={{ flex: 1 }} />
          <Space size={6}>
            <Text type="secondary" style={{ fontSize: 12 }}>{t("skills.change_status")}</Text>
            <Select<SkillStatus>
              value={skill.status}
              onChange={(v) => onChangeStatus(v)}
              style={{ width: 140 }}
              loading={statusSubmitting}
              disabled={statusSubmitting}
              data-testid="skill-status-select"
              options={STATUS_OPTIONS.map((s) => ({ value: s, label: s }))}
            />
          </Space>
        </div>
      </div>

      <Card title={t("skills.metadata_title")} size="small" style={{ marginBottom: 16 }}>
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
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("skills.col_category")}</dt>
          <dd style={{ margin: 0 }}>{skill.category}</dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("skills.col_description")}</dt>
          <dd style={{ margin: 0 }}>{skill.description}</dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("skills.col_created")}</dt>
          <dd style={{ margin: 0 }}>{new Date(skill.created_at).toLocaleString()}</dd>
          <dt style={{ color: "var(--hx-text-tertiary)" }}>{t("skills.col_updated")}</dt>
          <dd style={{ margin: 0 }}>{new Date(skill.updated_at).toLocaleString()}</dd>
        </dl>
      </Card>

      <Card title={t("skills.versions_title")} size="small">
        <Table<SkillVersion>
          columns={versionColumns}
          dataSource={versions}
          rowKey={(r) => r.id}
          pagination={false}
          size="small"
          locale={{ emptyText: <Empty description={t("skills.no_versions")} /> }}
          data-testid="skill-versions-table"
        />
      </Card>
    </div>
  );
}
