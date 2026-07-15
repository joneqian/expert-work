/**
 * UserProfile — Memory pane. The tenant-admin governance view of one
 * user's cross-agent long-term memory: list + edit (content / kind) +
 * forget (soft-delete), all threaded through ``?user_id=``.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { Pencil, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteMemory,
  listMemories,
  updateMemory,
  type MemoryItem,
  type MemoryKind,
  type MemoryList,
} from "../../api/memory";
import { errMessage } from "./useLoad";

const { Text } = Typography;

const KIND_OPTIONS: MemoryKind[] = ["fact", "episodic"];

export function MemoryPane({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [data, setData] = useState<MemoryList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [draftContent, setDraftContent] = useState("");
  const [draftKind, setDraftKind] = useState<MemoryKind>("fact");
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await listMemories({ userId }));
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openEdit = useCallback((record: MemoryItem) => {
    setEditing(record);
    setDraftContent(record.content);
    setDraftKind(record.kind);
  }, []);

  const submitEdit = useCallback(async () => {
    if (editing === null) return;
    setSaving(true);
    try {
      await updateMemory(editing.id, { content: draftContent, kind: draftKind }, userId);
      message.success(t("user_profile.memory_updated"));
      setEditing(null);
      await refresh();
    } catch (err) {
      message.error(errMessage(err));
    } finally {
      setSaving(false);
    }
  }, [editing, draftContent, draftKind, userId, message, t, refresh]);

  const handleForget = useCallback(
    async (id: string) => {
      setBusyId(id);
      try {
        await deleteMemory(id, userId);
        message.success(t("user_profile.memory_forgotten"));
        await refresh();
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        setBusyId(null);
      }
    },
    [userId, message, t, refresh],
  );

  const columns: TableColumnsType<MemoryItem> = useMemo(
    () => [
      { title: t("memory_tab.col_content"), dataIndex: "content", key: "content", ellipsis: true },
      {
        title: t("memory_tab.col_kind"),
        dataIndex: "kind",
        key: "kind",
        width: 110,
        render: (kind: MemoryKind) => (
          <Tag color={kind === "fact" ? "blue" : "purple"} bordered={false}>
            {kind}
          </Tag>
        ),
      },
      {
        title: t("user_profile.memory_col_importance"),
        dataIndex: "importance",
        key: "importance",
        width: 110,
        defaultSortOrder: "descend",
        sorter: (a, b) => a.importance - b.importance,
        render: (v: number) => (
          <Text style={{ fontVariantNumeric: "tabular-nums" }}>{v.toFixed(2)}</Text>
        ),
      },
      {
        title: t("user_profile.memory_col_confidence"),
        dataIndex: "confidence",
        key: "confidence",
        width: 100,
        sorter: (a, b) => a.confidence - b.confidence,
        render: (v: number) => (
          <Text style={{ fontVariantNumeric: "tabular-nums" }}>{v.toFixed(2)}</Text>
        ),
      },
      {
        title: t("memory_tab.col_created"),
        dataIndex: "created_at",
        key: "created_at",
        width: 190,
        sorter: (a, b) => a.created_at.localeCompare(b.created_at),
        render: (iso: string) => (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {new Date(iso).toLocaleString()}
          </Text>
        ),
      },
      {
        title: t("user_profile.memory_col_actions"),
        key: "actions",
        width: 160,
        render: (_: unknown, record) => (
          <Space size={6}>
            <Button
              size="small"
              icon={<Pencil size={13} strokeWidth={1.5} />}
              onClick={() => openEdit(record)}
              data-testid={`memory-edit-${record.id}`}
            >
              {t("user_profile.memory_edit")}
            </Button>
            <Popconfirm
              title={t("user_profile.memory_forget_confirm")}
              onConfirm={() => void handleForget(record.id)}
              okText={t("user_profile.memory_forget")}
              okButtonProps={{ danger: true }}
            >
              <Button
                size="small"
                danger
                icon={<Trash2 size={13} strokeWidth={1.5} />}
                loading={busyId === record.id}
                data-testid={`memory-forget-${record.id}`}
              >
                {t("user_profile.memory_forget")}
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    [t, busyId, openEdit, handleForget],
  );

  return (
    <div data-testid="user-memory-pane">
      {/* Cross-agent per-user memory (Mini-ADR H-13). */}
      <Alert
        type="info"
        showIcon
        message={t("user_detail.memory_scope_note")}
        style={{ marginBottom: 12 }}
      />
      {error !== null && <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />}
      <Table<MemoryItem>
        size="small"
        columns={columns}
        dataSource={data?.items ?? []}
        rowKey="id"
        loading={loading}
        pagination={false}
        locale={{ emptyText: <Empty description={t("user_detail.memory_empty")} /> }}
        data-testid="user-memory-table"
      />
      <Modal
        title={t("user_profile.memory_edit_title")}
        open={editing !== null}
        onOk={() => void submitEdit()}
        onCancel={() => setEditing(null)}
        confirmLoading={saving}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        data-testid="memory-edit-modal"
      >
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("user_profile.memory_edit_content")}
            </Text>
            <Input.TextArea
              value={draftContent}
              onChange={(e) => setDraftContent(e.target.value)}
              autoSize={{ minRows: 3, maxRows: 8 }}
              data-testid="memory-edit-content"
            />
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("user_profile.memory_edit_kind")}
            </Text>
            <div>
              <Select<MemoryKind>
                value={draftKind}
                onChange={(v) => setDraftKind(v)}
                options={KIND_OPTIONS.map((k) => ({ value: k, label: k }))}
                style={{ width: 160 }}
                data-testid="memory-edit-kind"
              />
            </div>
          </div>
        </Space>
      </Modal>
    </div>
  );
}
