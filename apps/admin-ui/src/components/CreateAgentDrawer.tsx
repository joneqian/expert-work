/**
 * Create Agent drawer — Stream H.2 PR 2.
 *
 * Antd Drawer hosting a Monaco YAML editor preloaded with a minimal
 * Agent manifest stub. On submit, POSTs to ``/v1/agents`` and lets the
 * backend's :class:`ManifestLoader` validate the payload end-to-end —
 * the same envelope errors that surface in ``ManifestTab`` apply here,
 * so the UI only renders them.
 *
 * On success the drawer closes and the parent decides what to do
 * (refresh list + optionally navigate to the new agent's detail page).
 */
import { useCallback, useState } from "react";
import { Alert, Button, Drawer, Space, Typography } from "antd";
import Editor from "@monaco-editor/react";
import { Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { createAgent, type AgentDetailResponse } from "../api/agents";

const { Text } = Typography;

/** Blank canvas — minimal valid AgentSpec shape. The user is expected
 *  to edit ``name`` / ``version`` / ``model`` before saving. Kept in
 *  sync with the integration-test fixtures so a copy-paste into the
 *  drawer always yields a valid manifest. */
export const DEFAULT_AGENT_YAML = `apiVersion: helix.io/v1
kind: Agent
metadata:
  name: my-agent
  version: "1.0.0"
  tenant: my-tenant
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-6
  system_prompt:
    template: "You are a helpful assistant."
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: []
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
`;

interface CreateAgentDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Fires after a successful POST so the parent can refresh the list
   *  or navigate. Receives the created agent's record. */
  onCreated: (record: AgentDetailResponse) => void;
}

export function CreateAgentDrawer({ open, onClose, onCreated }: CreateAgentDrawerProps) {
  const { t } = useTranslation();
  const [buffer, setBuffer] = useState<string>(DEFAULT_AGENT_YAML);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setBuffer(DEFAULT_AGENT_YAML);
    setError(null);
    setSubmitting(false);
  }, []);

  const handleCancel = useCallback(() => {
    reset();
    onClose();
  }, [reset, onClose]);

  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createAgent({ manifest_yaml: buffer });
      onCreated(created);
      reset();
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }, [buffer, onCreated, reset]);

  return (
    <Drawer
      open={open}
      onClose={handleCancel}
      title={
        <Space size={8}>
          <Plus size={16} strokeWidth={1.75} />
          {t("create_agent.title")}
        </Space>
      }
      width={720}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button
            icon={<X size={14} strokeWidth={1.75} />}
            onClick={handleCancel}
            disabled={submitting}
            data-testid="create-agent-cancel"
          >
            {t("create_agent.cancel")}
          </Button>
          <Button
            type="primary"
            icon={<Plus size={14} strokeWidth={1.75} />}
            onClick={handleSubmit}
            loading={submitting}
            data-testid="create-agent-submit"
          >
            {t("create_agent.submit")}
          </Button>
        </div>
      }
      data-testid="create-agent-drawer"
    >
      <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
        {t("create_agent.hint")}
      </Text>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("create_agent.create_failed")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="create-agent-error"
        />
      )}

      <Editor
        language="yaml"
        value={buffer}
        onChange={(v) => setBuffer(v ?? "")}
        theme="vs-dark"
        height="calc(100vh - 220px)"
        options={{
          minimap: { enabled: false },
          fontFamily: "var(--hx-font-mono)",
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
          renderWhitespace: "boundary",
          wordWrap: "on",
        }}
        data-testid="create-agent-editor"
      />
    </Drawer>
  );
}
