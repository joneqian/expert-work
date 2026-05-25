import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Checkbox,
  Col,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { TableColumnsType } from "antd";
import { AlertTriangle, ChevronRight, Copy, Key, MoreHorizontal, Plus, ShieldAlert } from "lucide-react";
import { ALL_SCOPES, generateMockKey, mockApiKeys, type MockApiKey } from "../mock/apiKeys";

const { Sider } = Layout;

const SETTINGS_MENU = [
  { key: "api-keys", label: "API Keys" },
  { key: "service-accounts", label: "Service Accounts" },
  { key: "role-bindings", label: "Role Bindings" },
  { key: "quotas", label: "Quotas" },
  { key: "tenant-config", label: "Tenant Config" },
  { key: "audit", label: "Audit" },
];

export function SettingsApiKeys() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { message } = App.useApp();

  const [keys, setKeys] = useState<MockApiKey[]>(mockApiKeys);
  const [createOpen, setCreateOpen] = useState(false);
  const [showOnce, setShowOnce] = useState<{ full: string; prefix: string } | null>(null);
  const [form] = Form.useForm();

  useEffect(() => {
    if (searchParams.get("action") === "create") {
      setCreateOpen(true);
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  const inRotation = useMemo(() => keys.filter((k) => k.status === "grace").length, [keys]);

  const columns: TableColumnsType<MockApiKey> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (n: string, r) => (
        <span>
          <span className="mono" style={{ fontWeight: 500 }}>{n}</span>
          {r.status === "grace" && (
            <span style={{ fontSize: 11, color: "var(--hx-text-tertiary)", marginLeft: 6 }}>(rotation old)</span>
          )}
        </span>
      ),
    },
    {
      title: "Prefix",
      dataIndex: "prefix",
      key: "prefix",
      width: 200,
      render: (p) => (
        <span
          className="mono"
          style={{
            background: "var(--hx-surface-raised)",
            padding: "2px 6px",
            borderRadius: 2,
            fontSize: 11,
            color: "var(--hx-text-tertiary)",
          }}
        >{p}</span>
      ),
    },
    {
      title: "Scopes",
      dataIndex: "scopes",
      key: "scopes",
      render: (s: string[]) => (
        <Space size={4} wrap>
          {s.map((scope) => (
            <Tag key={scope} bordered={false} style={{ borderRadius: 2 }}>{scope}</Tag>
          ))}
        </Space>
      ),
    },
    { title: "Service Account", dataIndex: "serviceAccount", key: "serviceAccount", width: 180 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 120,
      render: (s: MockApiKey["status"], r) => {
        if (s === "active") return <Tag color="success" bordered={false} style={{ borderRadius: 2 }}><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />active</Tag>;
        if (s === "grace") return <Tag color="warning" bordered={false} style={{ borderRadius: 2 }}><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />grace {r.graceUntil}</Tag>;
        return <Tag color="error" bordered={false} style={{ borderRadius: 2 }}><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />revoked</Tag>;
      },
    },
    { title: "Last used", dataIndex: "lastUsed", key: "lastUsed", width: 100, render: (u) => <span style={{ fontSize: 12, color: "var(--hx-text-secondary)" }}>{u}</span> },
    { title: "过期", dataIndex: "expiresAt", key: "expiresAt", width: 140, render: (e) => <span style={{ fontSize: 12, color: "var(--hx-text-secondary)" }}>{e}</span> },
    {
      title: "",
      key: "actions",
      width: 32,
      render: () => (
        <Tooltip title="更多操作">
          <Button type="text" size="small" icon={<MoreHorizontal size={14} strokeWidth={1.5} />} />
        </Tooltip>
      ),
    },
  ];

  return (
    <div>
      <Breadcrumb
        items={[{ title: "acme-corp" }, { title: "Settings" }, { title: "API Keys" }]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={<ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />}
      />

      <div className="hx-page-header">
        <div>
          <h1>API Keys</h1>
          <p style={{ margin: "8px 0 0", color: "var(--hx-text-secondary)" }}>
            服务账户的访问密钥;每个 key 绑定 scopes(权限范围)。支持轮换(双活窗口)与立即撤销。
          </p>
        </div>
        <Button type="primary" icon={<Plus size={14} strokeWidth={1.5} />} onClick={() => setCreateOpen(true)}>
          创建 API Key
        </Button>
      </div>

      <Row gutter={24}>
        <Col flex="200px">
          <Layout style={{ background: "transparent" }}>
            <Sider width={200} style={{ background: "transparent" }}>
              <Menu
                mode="inline"
                selectedKeys={["api-keys"]}
                items={SETTINGS_MENU}
                style={{ background: "transparent", border: "none" }}
              />
            </Sider>
          </Layout>
        </Col>
        <Col flex="auto">
          {inRotation > 0 && (
            <Alert
              showIcon
              icon={<AlertTriangle size={16} strokeWidth={1.5} />}
              type="warning"
              message={<strong>{inRotation} 个 key 处于轮换 grace 窗口</strong>}
              description="旧 key 仍可用;到期后失效。引导调用方尽快切到新 key。"
              action={<Button size="small">查看详情</Button>}
              style={{ marginBottom: 16 }}
            />
          )}

          <Table<MockApiKey>
            className="hx-table"
            rowKey="id"
            columns={columns}
            dataSource={keys}
            pagination={false}
          />
        </Col>
      </Row>

      {/* 创建 modal */}
      <Modal
        title="创建 API Key"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        okText="创建"
        cancelText="取消"
        onOk={() => {
          form
            .validateFields()
            .then((values: { name: string; serviceAccount: string; scopes: string[]; expiresIn: string }) => {
              const { full, prefix } = generateMockKey();
              const newKey: MockApiKey = {
                id: `key_${Date.now()}`,
                name: values.name,
                prefix,
                scopes: values.scopes,
                serviceAccount: values.serviceAccount,
                status: "active",
                lastUsed: "—",
                expiresAt: values.expiresIn === "never" ? "永不过期" : values.expiresIn,
              };
              setKeys((prev) => [newKey, ...prev]);
              setCreateOpen(false);
              form.resetFields();
              setShowOnce({ full, prefix });
            })
            .catch(() => undefined);
        }}
      >
        <Form
          form={form}
          layout="vertical"
          style={{ marginTop: 16 }}
          initialValues={{
            scopes: ["agents:read", "runs:read"],
            serviceAccount: "prod-ingest-svc",
            expiresIn: "2027-05-25",
          }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如 prod-ingest / staging-readonly" />
          </Form.Item>
          <Form.Item name="serviceAccount" label="绑定 Service Account" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "prod-ingest-svc", label: "prod-ingest-svc" },
                { value: "analyst-svc", label: "analyst-svc" },
                { value: "ci-bot-svc", label: "ci-bot-svc" },
              ]}
            />
          </Form.Item>
          <Form.Item name="scopes" label="Scopes(多选)" rules={[{ required: true, message: "至少选一个 scope" }]}>
            <Checkbox.Group>
              <Row>
                {ALL_SCOPES.map((s) => (
                  <Col key={s.value} span={12} style={{ marginBottom: 6 }}>
                    <Checkbox value={s.value}>
                      <span className="mono" style={{ fontSize: 12 }}>{s.label}</span>
                      {s.danger && <Tag color="error" bordered={false} style={{ marginLeft: 6, fontSize: 10 }}><ShieldAlert size={10} strokeWidth={1.5} style={{ verticalAlign: "middle", marginRight: 2 }} />危险</Tag>}
                    </Checkbox>
                  </Col>
                ))}
              </Row>
            </Checkbox.Group>
          </Form.Item>
          <Form.Item name="expiresIn" label="过期时间">
            <Select
              options={[
                { value: "2026-08-23", label: "90 天" },
                { value: "2026-11-21", label: "180 天" },
                { value: "2027-05-25", label: "1 年" },
                { value: "never", label: "永不过期 (不推荐)" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* show-once modal */}
      <Modal
        open={!!showOnce}
        onCancel={() => setShowOnce(null)}
        title={<span style={{ color: "var(--hx-color-brand-400)" }}><Key size={16} strokeWidth={1.5} style={{ verticalAlign: "middle", marginRight: 6 }} />API Key 已创建</span>}
        okText="我已保存,关闭"
        onOk={() => setShowOnce(null)}
        cancelButtonProps={{ style: { display: "none" } }}
      >
        {showOnce && (
          <div style={{ marginTop: 12 }}>
            <p>请立即复制此 key 并安全保存 — <strong>窗口关闭后无法再次查看完整 key</strong>。</p>
            <div
              style={{
                fontFamily: "var(--hx-font-mono)",
                fontSize: 14,
                padding: 12,
                background: "var(--hx-color-neutral-950)",
                border: "1px solid var(--hx-border-default)",
                borderRadius: 6,
                display: "flex",
                gap: 12,
                margin: "12px 0",
              }}
            >
              <code style={{ flex: 1, wordBreak: "break-all" }}>{showOnce.full}</code>
              <Button
                size="small"
                icon={<Copy size={12} strokeWidth={1.5} />}
                onClick={() => {
                  navigator.clipboard.writeText(showOnce.full);
                  message.success("已复制到剪贴板");
                }}
              >
                复制
              </Button>
            </div>
            <p style={{ color: "var(--hx-text-secondary)", fontSize: 13, margin: 0 }}>
              prefix <code className="mono">{showOnce.prefix}</code> 会保留显示在列表中,完整 key 仅此一次。
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
