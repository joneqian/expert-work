/**
 * Knowledge page — Stream H.7 PR 1 (design § 6.9).
 *
 * Tenant-scoped knowledge-base governance over ``/v1/knowledge``:
 * single-page master-detail (Mini-ADR H-17) — bases on the left,
 * the selected base's documents on the right with async-ingest status.
 *
 * Ingest progress is a conditional poll (Mini-ADR H-18): the upload
 * endpoint 202s and there is no push channel, so while the selected
 * base has pending/ingesting documents the list refreshes every 5s and
 * stops once all rows are terminal.
 *
 * Tenant semantics (Mini-ADR H-19): the backend reads the JWT's home
 * tenant only — this page does NOT follow the global TenantScope
 * switch, and says so when the scope is non-home.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { TableColumnsType } from "antd";
import { BookOpen, Plus, Trash2, UploadCloud } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  createBase,
  deleteBase,
  deleteDocument,
  isSupportedDocument,
  listBases,
  listDocuments,
  SUPPORTED_DOCUMENT_EXTENSIONS,
  uploadDocument,
  type DocumentStatus,
  type KnowledgeBase,
  type KnowledgeDocument,
} from "../api/knowledge";
import { ApiError } from "../api/client";
import { useTenantScope } from "../tenant/TenantScopeContext";
import { PageHeader } from "../components/PageHeader";

const { Text } = Typography;

const STATUS_COLOR: Record<DocumentStatus, string> = {
  pending: "default",
  ingesting: "processing",
  ready: "success",
  failed: "error",
};

const POLL_INTERVAL_MS = 5_000;

function errMessage(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}

interface CreateFormValues {
  name: string;
  chunkMaxTokens?: number;
  chunkOverlapTokens?: number;
}

export function KnowledgeAdmin() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { scope } = useTenantScope();

  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [basesLoading, setBasesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<CreateFormValues>();
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshBases = useCallback(async () => {
    setBasesLoading(true);
    setError(null);
    try {
      const result = await listBases();
      setBases(result);
    } catch (err) {
      setError(errMessage(err));
    } finally {
      setBasesLoading(false);
    }
  }, []);

  const refreshDocuments = useCallback(
    async (baseName: string, { quiet = false }: { quiet?: boolean } = {}) => {
      if (!quiet) setDocumentsLoading(true);
      try {
        const result = await listDocuments(baseName);
        setDocuments(result);
      } catch (err) {
        message.error(errMessage(err));
      } finally {
        if (!quiet) setDocumentsLoading(false);
      }
    },
    [message],
  );

  useEffect(() => {
    void refreshBases();
  }, [refreshBases]);

  useEffect(() => {
    if (selected !== null) {
      setDocuments([]);
      void refreshDocuments(selected);
    }
  }, [selected, refreshDocuments]);

  // Mini-ADR H-18 — poll only while the selected base has non-terminal
  // documents; clear on base switch / unmount / all-terminal.
  const hasActiveIngest = useMemo(
    () => documents.some((d) => d.status === "pending" || d.status === "ingesting"),
    [documents],
  );

  useEffect(() => {
    if (selected !== null && hasActiveIngest) {
      pollTimer.current = setInterval(() => {
        void refreshDocuments(selected, { quiet: true });
      }, POLL_INTERVAL_MS);
      return () => {
        if (pollTimer.current !== null) clearInterval(pollTimer.current);
        pollTimer.current = null;
      };
    }
    return undefined;
  }, [selected, hasActiveIngest, refreshDocuments]);

  const handleCreate = useCallback(
    async (values: CreateFormValues) => {
      setCreating(true);
      try {
        await createBase(values);
        setCreateOpen(false);
        form.resetFields();
        await refreshBases();
      } catch (err) {
        const detail =
          err instanceof ApiError && err.status === 409
            ? t("knowledge_page.create_duplicate")
            : errMessage(err);
        message.error(detail);
      } finally {
        setCreating(false);
      }
    },
    [t, form, message, refreshBases],
  );

  const handleDeleteBase = useCallback(
    async (name: string) => {
      try {
        await deleteBase(name);
        if (selected === name) {
          setSelected(null);
          setDocuments([]);
        }
        await refreshBases();
      } catch (err) {
        message.error(errMessage(err));
      }
    },
    [selected, message, refreshBases],
  );

  const handleUpload = useCallback(
    async (file: File) => {
      if (selected === null) return false;
      if (!isSupportedDocument(file.name)) {
        message.error(t("knowledge_page.unsupported_type", { name: file.name }));
        return false;
      }
      setUploading(true);
      try {
        await uploadDocument(selected, file);
        await refreshDocuments(selected);
      } catch (err) {
        const detail =
          err instanceof ApiError && err.status === 503
            ? t("knowledge_page.embedder_missing")
            : errMessage(err);
        message.error(detail);
      } finally {
        setUploading(false);
      }
      return false; // we handled the upload ourselves
    },
    [t, selected, message, refreshDocuments],
  );

  const handleDeleteDocument = useCallback(
    async (documentId: string) => {
      if (selected === null) return;
      try {
        await deleteDocument(selected, documentId);
        await refreshDocuments(selected);
      } catch (err) {
        message.error(errMessage(err));
      }
    },
    [selected, message, refreshDocuments],
  );

  const baseColumns: TableColumnsType<KnowledgeBase> = useMemo(
    () => [
      {
        title: t("knowledge_page.col_base_name"),
        dataIndex: "name",
        key: "name",
        render: (name: string) => <Text strong>{name}</Text>,
      },
      {
        title: t("knowledge_page.col_chunking"),
        key: "chunking",
        width: 130,
        render: (_: unknown, record) => (
          <Text type="secondary" style={{ fontSize: 12 }} className="mono">
            {record.chunk_max_tokens}/{record.chunk_overlap_tokens}
          </Text>
        ),
      },
      {
        title: "",
        key: "actions",
        width: 60,
        render: (_: unknown, record) => (
          <Popconfirm
            title={t("knowledge_page.delete_base_confirm_title", { name: record.name })}
            description={t("knowledge_page.delete_base_confirm_body")}
            onConfirm={() => void handleDeleteBase(record.name)}
            okText={t("knowledge_page.delete")}
            okButtonProps={{ danger: true }}
          >
            <Button
              size="small"
              danger
              type="text"
              icon={<Trash2 size={13} strokeWidth={1.5} />}
              onClick={(e) => e.stopPropagation()}
              aria-label={t("knowledge_page.delete")}
              data-testid={`kb-delete-${record.name}`}
            />
          </Popconfirm>
        ),
      },
    ],
    [t, handleDeleteBase],
  );

  const documentColumns: TableColumnsType<KnowledgeDocument> = useMemo(
    () => [
      {
        title: t("knowledge_page.col_filename"),
        dataIndex: "filename",
        key: "filename",
        ellipsis: true,
        render: (filename: string) => <Text strong>{filename}</Text>,
      },
      {
        title: t("knowledge_page.col_status"),
        dataIndex: "status",
        key: "status",
        width: 120,
        render: (status: DocumentStatus, record) =>
          status === "failed" && record.error ? (
            <Tooltip title={record.error}>
              <Tag color={STATUS_COLOR[status]}>{status}</Tag>
            </Tooltip>
          ) : (
            <Tag color={STATUS_COLOR[status]}>{status}</Tag>
          ),
      },
      {
        title: t("knowledge_page.col_chunks"),
        dataIndex: "chunk_count",
        key: "chunk_count",
        width: 90,
        render: (count: number) => <Text className="mono">{count}</Text>,
      },
      {
        title: t("knowledge_page.col_updated"),
        dataIndex: "updated_at",
        key: "updated_at",
        width: 180,
        render: (iso: string | null) =>
          iso ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {new Date(iso).toLocaleString()}
            </Text>
          ) : (
            <Text type="secondary">—</Text>
          ),
      },
      {
        title: "",
        key: "actions",
        width: 60,
        render: (_: unknown, record) => (
          <Popconfirm
            title={t("knowledge_page.delete_doc_confirm_title", { name: record.filename })}
            onConfirm={() => void handleDeleteDocument(record.id)}
            okText={t("knowledge_page.delete")}
            okButtonProps={{ danger: true }}
          >
            <Button
              size="small"
              danger
              type="text"
              icon={<Trash2 size={13} strokeWidth={1.5} />}
              aria-label={t("knowledge_page.delete")}
              data-testid={`doc-delete-${record.id}`}
            />
          </Popconfirm>
        ),
      },
    ],
    [t, handleDeleteDocument],
  );

  return (
    <div data-testid="knowledge-root">
      <PageHeader
        icon={<BookOpen size={18} strokeWidth={1.5} />}
        title={t("knowledge_page.page_title")}
        subtitle={
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t("knowledge_page.subtitle")}
          </Text>
        }
        actions={
          <Button
            type="primary"
            icon={<Plus size={14} strokeWidth={1.5} />}
            onClick={() => setCreateOpen(true)}
            data-testid="kb-create-open"
          >
            {t("knowledge_page.create_base")}
          </Button>
        }
      />

      {scope !== undefined && (
        <Alert
          type="info"
          showIcon
          message={t("knowledge_page.home_scope_note")}
          style={{ marginBottom: 16 }}
          data-testid="knowledge-scope-note"
        />
      )}

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("knowledge_page.failed_to_load")}
          description={error}
          style={{ marginBottom: 16 }}
          data-testid="knowledge-error"
        />
      )}

      <Row gutter={16}>
        <Col span={9}>
          <Card size="small" title={t("knowledge_page.bases_title")}>
            <Table<KnowledgeBase>
              size="small"
              columns={baseColumns}
              dataSource={bases}
              rowKey="name"
              loading={basesLoading}
              pagination={false}
              onRow={(record) => ({
                onClick: () => setSelected(record.name),
                style: { cursor: "pointer" },
              })}
              rowClassName={(record) => (record.name === selected ? "ant-table-row-selected" : "")}
              locale={{ emptyText: <Empty description={t("knowledge_page.bases_empty")} /> }}
              data-testid="kb-table"
            />
          </Card>
        </Col>
        <Col span={15}>
          <Card
            size="small"
            title={
              selected !== null
                ? t("knowledge_page.documents_title", { name: selected })
                : t("knowledge_page.documents_unselected")
            }
            extra={
              selected !== null && (
                <Upload
                  accept={SUPPORTED_DOCUMENT_EXTENSIONS.join(",")}
                  showUploadList={false}
                  beforeUpload={(file) => handleUpload(file)}
                >
                  <Button
                    size="small"
                    icon={<UploadCloud size={13} strokeWidth={1.5} />}
                    loading={uploading}
                    data-testid="doc-upload"
                  >
                    {t("knowledge_page.upload")}
                  </Button>
                </Upload>
              )
            }
          >
            {selected === null ? (
              <Empty description={t("knowledge_page.select_hint")} />
            ) : (
              <Table<KnowledgeDocument>
                size="small"
                columns={documentColumns}
                dataSource={documents}
                rowKey="id"
                loading={documentsLoading}
                pagination={false}
                locale={{
                  emptyText: <Empty description={t("knowledge_page.documents_empty")} />,
                }}
                data-testid="doc-table"
              />
            )}
          </Card>
        </Col>
      </Row>

      <Modal
        title={t("knowledge_page.create_base")}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={creating}
        okText={t("common.save")}
        data-testid="kb-create-modal"
      >
        <Form<CreateFormValues> form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item
            name="name"
            label={t("knowledge_page.field_name")}
            rules={[{ required: true, min: 1, max: 128 }]}
          >
            <Input data-testid="kb-create-name" />
          </Form.Item>
          <Form.Item
            name="chunkMaxTokens"
            label={t("knowledge_page.field_chunk_max")}
            tooltip={t("knowledge_page.field_chunk_hint")}
          >
            <InputNumber min={1} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item
            name="chunkOverlapTokens"
            label={t("knowledge_page.field_chunk_overlap")}
            tooltip={t("knowledge_page.field_chunk_hint")}
          >
            <InputNumber min={0} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
