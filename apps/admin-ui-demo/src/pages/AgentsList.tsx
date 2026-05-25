import { useMemo, useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Breadcrumb,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  App,
} from "antd";
import type { TableColumnsType } from "antd";
import { Plus, Upload, Search, MoreHorizontal, ChevronRight, Bot } from "lucide-react";
import { mockAgents, type AgentStatus, type MockAgent } from "../mock/agents";

const STATUS_BADGE: Record<AgentStatus, { color: string; text: string }> = {
  active: { color: "success", text: "active" },
  draft: { color: "warning", text: "draft" },
  archived: { color: "default", text: "archived" },
};

export function AgentsList() {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { message } = App.useApp();

  const [showEmpty, setShowEmpty] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  // 支持 Cmd+K 触发 ?action=create
  useEffect(() => {
    if (searchParams.get("action") === "create") {
      setCreateOpen(true);
      setSearchParams({});
    }
  }, [searchParams, setSearchParams]);

  const data = useMemo(() => {
    if (showEmpty) return [];
    return mockAgents.filter((a) => {
      if (statusFilter !== "all" && a.status !== statusFilter) return false;
      if (query && !a.name.includes(query) && !a.description.includes(query)) return false;
      return true;
    });
  }, [showEmpty, statusFilter, query]);

  const columns: TableColumnsType<MockAgent> = [
    {
      title: "Name",
      dataIndex: "name",
      key: "name",
      render: (_, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{r.name}</div>
          <div style={{ fontSize: 11, color: "var(--hx-text-tertiary)", marginTop: 2 }}>{r.description}</div>
        </div>
      ),
    },
    {
      title: "Status",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (s: AgentStatus) => (
        <Tag color={STATUS_BADGE[s].color} bordered={false} style={{ borderRadius: 2 }}>
          <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: 999, background: "currentColor", marginRight: 4 }} />
          {STATUS_BADGE[s].text}
        </Tag>
      ),
    },
    { title: "Model", dataIndex: "model", key: "model", width: 180, render: (m) => <span className="mono" style={{ color: "var(--hx-text-secondary)", fontSize: 12 }}>{m}</span> },
    { title: "Version", dataIndex: "version", key: "version", width: 80, render: (v) => <span className="mono" style={{ fontSize: 12 }}>{v}</span> },
    {
      title: "本月 Runs",
      dataIndex: "monthlyRuns",
      key: "monthlyRuns",
      width: 110,
      align: "right",
      render: (n: number) => <span className="mono">{n.toLocaleString()}</span>,
    },
    {
      title: "失败率",
      dataIndex: "failureRate",
      key: "failureRate",
      width: 90,
      align: "right",
      render: (r: number) => {
        if (r === 0) return <span className="mono" style={{ color: "var(--hx-color-success-500)" }}>0.0%</span>;
        const color = r > 0.05 ? "var(--hx-color-danger-500)" : r > 0.02 ? "var(--hx-color-warning-500)" : "var(--hx-color-success-500)";
        return <span className="mono" style={{ color }}>{(r * 100).toFixed(1)}%</span>;
      },
    },
    {
      title: "P95 延迟",
      dataIndex: "p95LatencyMs",
      key: "p95LatencyMs",
      width: 90,
      align: "right",
      render: (ms: number) => (ms === 0 ? <span className="mono" style={{ color: "var(--hx-text-tertiary)" }}>—</span> : <span className="mono">{(ms / 1000).toFixed(2)}s</span>),
    },
    { title: "Updated", dataIndex: "updatedAt", key: "updatedAt", width: 100, render: (u) => <span style={{ fontSize: 12, color: "var(--hx-text-secondary)" }}>{u}</span> },
    {
      title: "",
      key: "actions",
      width: 32,
      render: () => (
        <Tooltip title="更多操作">
          <Button type="text" size="small" icon={<MoreHorizontal size={14} strokeWidth={1.5} />} onClick={(e) => e.stopPropagation()} />
        </Tooltip>
      ),
    },
  ];

  return (
    <div>
      <Breadcrumb
        items={[{ title: "acme-corp" }, { title: "Agents" }]}
        style={{ marginBottom: 8, fontSize: 13 }}
        separator={<ChevronRight size={12} strokeWidth={1.5} style={{ verticalAlign: "middle" }} />}
      />

      <div className="hx-page-header">
        <div>
          <h1>Agents</h1>
          <p style={{ margin: "8px 0 0", color: "var(--hx-text-secondary)" }}>
            租户内所有 Agent 定义,可创建、编辑 manifest、查看 runs / 触发器 / 记忆
          </p>
        </div>
        <Space>
          <Button icon={<Upload size={14} strokeWidth={1.5} />}>导入 YAML</Button>
          <Button type="primary" icon={<Plus size={14} strokeWidth={1.5} />} onClick={() => setCreateOpen(true)}>
            创建 Agent
            <span className="hx-kbd" style={{ marginLeft: 6 }}>N</span>
          </Button>
        </Space>
      </div>

      {/* Toolbar */}
      <Space style={{ marginBottom: 16, width: "100%" }}>
        <Input
          allowClear
          placeholder="搜索 agent name / id / model"
          prefix={<Search size={14} strokeWidth={1.5} />}
          style={{ width: 280 }}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Select
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "all", label: "所有状态" },
            { value: "active", label: "active" },
            { value: "draft", label: "draft" },
            { value: "archived", label: "archived" },
          ]}
        />
        <Select
          style={{ width: 180 }}
          defaultValue="all-models"
          options={[
            { value: "all-models", label: "所有模型" },
            { value: "claude-opus-4-7", label: "claude-opus-4-7" },
            { value: "claude-sonnet-4-6", label: "claude-sonnet-4-6" },
            { value: "claude-haiku-4-5", label: "claude-haiku-4-5" },
          ]}
        />
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 13, color: "var(--hx-text-tertiary)" }}>{data.length} agents</span>
        <Tooltip title="演示空态切换">
          <Switch
            size="small"
            checked={showEmpty}
            onChange={setShowEmpty}
            checkedChildren="空"
            unCheckedChildren="默认"
          />
        </Tooltip>
      </Space>

      <Table<MockAgent>
        className="hx-table"
        rowKey="id"
        columns={columns}
        dataSource={data}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        onRow={(record) => ({
          onClick: () => nav(`/agents/${record.id}/overview`),
          style: { cursor: "pointer" },
        })}
        locale={{
          emptyText: (
            <div style={{ padding: "64px 24px", textAlign: "center" }}>
              <Bot size={32} strokeWidth={1.5} style={{ color: "var(--hx-text-tertiary)", opacity: 0.4, marginBottom: 12 }} />
              <div style={{ fontSize: 16, fontWeight: 600, color: "var(--hx-text-primary)", marginBottom: 4 }}>还没有 Agent</div>
              <div style={{ fontSize: 13, color: "var(--hx-text-secondary)", marginBottom: 16 }}>
                创建第一个 Agent 开始定义你的智能体能力。也可以从 YAML 导入既有 manifest。
              </div>
              <Space>
                <Button type="primary" onClick={() => setCreateOpen(true)}>创建 Agent</Button>
                <Button>导入 YAML</Button>
              </Space>
            </div>
          ),
        }}
      />

      {/* 创建 modal */}
      <Modal
        title="创建 Agent"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => {
          form.validateFields().then(() => {
            message.success("Agent 已创建(demo:仅前端模拟)");
            setCreateOpen(false);
            form.resetFields();
          });
        }}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="如 customer-support-bot;小写 + 连字符" />
          </Form.Item>
          <Form.Item name="model" label="主模型" rules={[{ required: true }]}>
            <Select
              placeholder="选择主模型"
              options={[
                { value: "claude-opus-4-7", label: "claude-opus-4-7" },
                { value: "claude-sonnet-4-6", label: "claude-sonnet-4-6" },
                { value: "claude-haiku-4-5", label: "claude-haiku-4-5" },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="一句话描述这个 agent 做什么" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
