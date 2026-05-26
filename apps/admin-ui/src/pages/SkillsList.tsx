/**
 * Skills list page — Stream H.4 PR 5.
 *
 * Cursor-paginated skill library + Import ZIP + Create drawer (Monaco
 * YAML stub — the prompt fragment / tool names / required models live
 * in version rows, so Create here just makes an empty draft skill).
 *
 * Cross-tenant scope inherits from ``TenantScopeContext`` (system_admin
 * can ``tenant_id=*``).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import {
  ChevronRight,
  FileCode2,
  Globe2,
  Plus,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import {
  createSkill,
  importSkillZip,
  listSkills,
  type SkillList,
  type SkillRecord,
  type SkillStatus,
} from "../api/skills";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";

const { Text } = Typography;

const STATUS_OPTIONS: SkillStatus[] = ["draft", "active", "archived"];

const STATUS_COLOR: Record<SkillStatus, string> = {
  draft: "default",
  active: "success",
  archived: "warning",
};

export function SkillsList() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope, apiTenantScope } = useTenantScope();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [data, setData] = useState<SkillList | null>(null);
  const [accumulated, setAccumulated] = useState<SkillRecord[]>([]);
  const [statusFilter, setStatusFilter] = useState<SkillStatus | undefined>(undefined);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createForm] = Form.useForm<{ name: string; description: string; category: string }>();

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await listSkills({
        tenantScope: apiTenantScope,
        status: statusFilter,
        category: categoryFilter.trim().length > 0 ? categoryFilter.trim() : undefined,
      });
      setData(result);
      setAccumulated(result.items);
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
  }, [apiTenantScope, statusFilter, categoryFilter]);

  const loadMore = useCallback(async () => {
    if (data?.next_cursor === undefined || data?.next_cursor === null) return;
    setLoadingMore(true);
    try {
      const result = await listSkills({
        tenantScope: apiTenantScope,
        status: statusFilter,
        category: categoryFilter.trim().length > 0 ? categoryFilter.trim() : undefined,
        cursor: data.next_cursor,
      });
      setData(result);
      setAccumulated((prev) => [...prev, ...result.items]);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "failed");
    } finally {
      setLoadingMore(false);
    }
  }, [apiTenantScope, statusFilter, categoryFilter, data, message]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = useCallback(async () => {
    const values = await createForm.validateFields();
    setCreateSubmitting(true);
    try {
      const created = await createSkill(values);
      message.success(t("skills.created"));
      setCreateOpen(false);
      createForm.resetFields();
      navigate(`/skills/${encodeURIComponent(created.id)}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "failed");
    } finally {
      setCreateSubmitting(false);
    }
  }, [createForm, message, navigate, t]);

  const onImportClick = useCallback(() => fileInputRef.current?.click(), []);

  const onImportFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const result = await importSkillZip(file);
        message.success(
          t("skills.imported", {
            name: result.skill.name,
            version: result.version.version,
          }),
        );
        refresh();
      } catch (err) {
        message.error(err instanceof Error ? err.message : "import failed");
      } finally {
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [message, refresh, t],
  );

  const isCrossTenant = data?.cross_tenant ?? false;
  const hasMore = data?.next_cursor !== null && data?.next_cursor !== undefined;

  const columns: TableColumnsType<SkillRecord> = useMemo(() => [
    {
      title: t("skills.col_name"),
      dataIndex: "name",
      key: "name",
      render: (v: string, record) => (
        <Space size={6}>
          <Text strong>{v}</Text>
          {record.latest_version !== null && (
            <Tag bordered={false}>v{record.latest_version}</Tag>
          )}
        </Space>
      ),
    },
    {
      title: t("skills.col_status"),
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (s: SkillStatus) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
    },
    {
      title: t("skills.col_category"),
      dataIndex: "category",
      key: "category",
      width: 160,
    },
    {
      title: t("skills.col_description"),
      dataIndex: "description",
      key: "description",
      ellipsis: true,
      render: (text: string) => (
        <Tooltip title={text} mouseEnterDelay={0.4}>
          <Text style={{ fontSize: 12 }}>{text}</Text>
        </Tooltip>
      ),
    },
    {
      title: t("skills.col_updated"),
      dataIndex: "updated_at",
      key: "updated_at",
      width: 180,
      render: (iso: string) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{new Date(iso).toLocaleString()}</Text>
      ),
    },
  ], [t]);

  return (
    <div data-testid="skills-root">
      <div className="hx-page-header">
        <Breadcrumb
          separator={<ChevronRight size={12} strokeWidth={1.5} />}
          items={[{ title: t("common.home") }, { title: t("skills.page_title") }]}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8, marginBottom: 16 }}>
          <FileCode2 size={20} strokeWidth={1.5} />
          <h1 style={{ margin: 0 }}>{t("skills.page_title")}</h1>
          {isCrossTenant && (
            <Tag icon={<Globe2 size={12} strokeWidth={1.5} />} color="purple" data-testid="skills-cross-banner">
              {t("skills.cross_tenant_banner")}
            </Tag>
          )}
          <span style={{ flex: 1 }} />
          <Select<SkillStatus | "all">
            value={statusFilter ?? "all"}
            onChange={(v) => setStatusFilter(v === "all" ? undefined : (v as SkillStatus))}
            style={{ width: 140 }}
            aria-label={t("skills.filter_status")}
            data-testid="skills-status-filter"
            options={[
              { value: "all", label: t("skills.filter_status_all") },
              ...STATUS_OPTIONS.map((s) => ({ value: s, label: s })),
            ]}
          />
          <Input
            placeholder={t("skills.filter_category")}
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            style={{ width: 160 }}
            allowClear
            data-testid="skills-category-filter"
          />
          <Button onClick={refresh} loading={loading} icon={<RefreshCw size={14} strokeWidth={1.5} />}>
            {t("common.refresh")}
          </Button>
          <Button onClick={onImportClick} icon={<Upload size={14} strokeWidth={1.75} />} data-testid="skills-import-btn">
            {t("skills.import_zip")}
          </Button>
          <Button type="primary" icon={<Plus size={14} strokeWidth={1.75} />} onClick={() => setCreateOpen(true)} data-testid="skills-create-btn">
            {t("skills.create")}
          </Button>
        </div>
        <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: "0 0 12px" }}>
          {t("skills.subtitle")}
        </p>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept=".zip,.skill,application/zip"
        style={{ display: "none" }}
        onChange={onImportFile}
        data-testid="skills-import-input"
      />

      {error !== null && (
        <Alert type="error" showIcon message={t("skills.failed_to_load")} description={error} style={{ marginBottom: 12 }} data-testid="skills-error" />
      )}

      <Table<SkillRecord>
        columns={columns}
        dataSource={accumulated}
        rowKey={(r) => r.id}
        loading={loading}
        pagination={false}
        onRow={(record) => ({
          onClick: () => navigate(`/skills/${encodeURIComponent(record.id)}`),
          style: { cursor: "pointer" },
        })}
        locale={{
          emptyText: (
            <Empty description={scope === "*" ? t("skills.empty_cross") : t("skills.empty_home")} />
          ),
        }}
        data-testid="skills-table"
      />

      {hasMore && (
        <div style={{ display: "flex", justifyContent: "center", marginTop: 16 }}>
          <Button onClick={loadMore} loading={loadingMore} data-testid="skills-load-more">
            {t("skills.load_more")}
          </Button>
        </div>
      )}

      <Drawer
        title={t("skills.create_modal_title")}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={520}
        data-testid="skills-create-drawer"
        extra={
          <Space>
            <Button onClick={() => setCreateOpen(false)}>{t("common.cancel")}</Button>
            <Button type="primary" loading={createSubmitting} onClick={onCreate}>
              {t("common.save")}
            </Button>
          </Space>
        }
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="name"
            label={t("skills.field_name")}
            rules={[{ required: true, message: t("skills.name_required") }]}
          >
            <Input data-testid="skills-name-input" maxLength={64} placeholder="e.g. web_search" />
          </Form.Item>
          <Form.Item
            name="category"
            label={t("skills.field_category")}
            rules={[{ required: true, message: t("skills.category_required") }]}
          >
            <Input data-testid="skills-category-input" placeholder="web" />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("skills.field_description")}
            rules={[{ required: true, message: t("skills.description_required") }]}
          >
            <Input.TextArea data-testid="skills-description-input" rows={4} />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("skills.create_hint")}
          </Text>
        </Form>
      </Drawer>
    </div>
  );
}
