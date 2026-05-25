/**
 * Manifest tab — Stream H.2 PR 1.
 *
 * Monaco YAML editor with read / edit / save flow against
 * ``PUT /v1/agents/{name}/{version}``. The backend re-runs the full
 * :class:`ManifestLoader` pipeline so we never need a client-side
 * validation pass — server errors flow back through the envelope.
 *
 * View mode is the default: the YAML is rendered read-only so casual
 * navigation through the tab never risks a stray keystroke. The user
 * has to click ``Edit`` to mutate; ``Cancel`` re-derives the buffer
 * from the latest server snapshot, dropping any in-flight edits.
 */
import { useCallback, useMemo, useState } from "react";
import { Alert, Button, Card, Space, Typography } from "antd";
import Editor from "@monaco-editor/react";
import { dump as yamlDump } from "js-yaml";
import { Edit3, Save, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { updateAgent, type AgentDetailResponse } from "../../api/agents";

const { Text } = Typography;

interface ManifestTabProps {
  detail: AgentDetailResponse;
  /** Called after a successful save — parent refetches so the SHA, the
   *  ``updated_at`` timestamp, and any server-side coercion show up. */
  onSaved: () => void;
}

export function ManifestTab({ detail, onSaved }: ManifestTabProps) {
  const { t } = useTranslation();
  const r = detail.record;

  /** Server snapshot serialised as YAML. Kept in a memo so re-renders
   *  during ``view`` mode don't churn the editor's internal state. */
  const snapshotYaml = useMemo(() => yamlDump(r.spec, { lineWidth: 120 }), [r.spec]);

  const [mode, setMode] = useState<"view" | "edit">("view");
  const [buffer, setBuffer] = useState<string>(snapshotYaml);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEdit = useCallback(() => {
    setBuffer(snapshotYaml);
    setError(null);
    setMode("edit");
  }, [snapshotYaml]);

  const handleCancel = useCallback(() => {
    setBuffer(snapshotYaml);
    setError(null);
    setMode("view");
  }, [snapshotYaml]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      await updateAgent(r.name, r.version, { manifest_yaml: buffer });
      setMode("view");
      onSaved();
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      setError(message);
    } finally {
      setSaving(false);
    }
  }, [buffer, r.name, r.version, onSaved]);

  return (
    <Card data-testid="manifest-tab">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          {mode === "view" ? t("manifest_tab.read_only_hint") : t("manifest_tab.edit_hint")}
        </Text>
        <Space size={8}>
          {mode === "view" ? (
            <Button
              size="small"
              icon={<Edit3 size={14} strokeWidth={1.75} />}
              onClick={handleEdit}
              data-testid="manifest-edit-btn"
            >
              {t("manifest_tab.edit")}
            </Button>
          ) : (
            <>
              <Button
                size="small"
                icon={<X size={14} strokeWidth={1.75} />}
                onClick={handleCancel}
                disabled={saving}
                data-testid="manifest-cancel-btn"
              >
                {t("manifest_tab.cancel")}
              </Button>
              <Button
                size="small"
                type="primary"
                icon={<Save size={14} strokeWidth={1.75} />}
                onClick={handleSave}
                loading={saving}
                data-testid="manifest-save-btn"
              >
                {t("manifest_tab.save")}
              </Button>
            </>
          )}
        </Space>
      </div>

      {error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("manifest_tab.save_failed")}
          description={error}
          style={{ marginBottom: 12 }}
          data-testid="manifest-error"
        />
      )}

      <Editor
        language="yaml"
        value={mode === "view" ? snapshotYaml : buffer}
        onChange={(v) => setBuffer(v ?? "")}
        theme="vs-dark"
        height="calc(100vh - 360px)"
        options={{
          readOnly: mode === "view",
          minimap: { enabled: false },
          fontFamily: "var(--hx-font-mono)",
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
          renderWhitespace: "boundary",
          wordWrap: "on",
        }}
        data-testid="manifest-editor"
      />
    </Card>
  );
}
