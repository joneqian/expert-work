/**
 * Approval card — Stream H.3 PR 5 (Mini-ADR H-9).
 *
 * Extracted from the inline ``<Alert>`` in :ref:`RunDetail` for the
 * three reasons the design doc named:
 *
 *   1. ``override_args`` editing — the reviewer can fix the agent's
 *      proposed JSON inline before approving.
 *   2. Pristine vs edited buffer — the Approve button's label switches
 *      between "Approve" and "Approve with edits" based on whether
 *      the buffer was actually touched.
 *   3. JSON.parse client-side validation — Approve is disabled when
 *      the buffer is syntactically invalid so we never POST garbage.
 *
 * The card emits ``onResolved`` once the resume POST succeeds so the
 * parent can refresh / clear local state.
 */
import { useCallback, useMemo, useState } from "react";
import { Alert, App, Button, Card, Space, Tooltip, Typography } from "antd";
import Editor from "@monaco-editor/react";
import { AlertTriangle, Check, Edit3, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { resumeRun, type PendingApproval } from "../../api/runs";

const { Text } = Typography;

interface ApprovalCardProps {
  threadId: string;
  runId: string;
  approval: PendingApproval;
  onResolved: () => void;
}

export function ApprovalCard({ threadId, runId, approval, onResolved }: ApprovalCardProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();

  /** Server-side proposed args, frozen for the lifetime of this card.
   *  When the user clicks "Cancel edit" we re-derive the buffer from
   *  this snapshot, not from a stale React state value. */
  const pristineJson = useMemo(
    () => JSON.stringify(approval.proposed_args, null, 2),
    [approval.proposed_args],
  );

  const [editing, setEditing] = useState(false);
  const [buffer, setBuffer] = useState(pristineJson);
  const [submitting, setSubmitting] = useState(false);

  const parseResult = useMemo<
    | { ok: true; value: Record<string, unknown> }
    | { ok: false; error: string }
  >(() => {
    try {
      const parsed = JSON.parse(buffer);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return { ok: false, error: t("approval_card.json_must_be_object") };
      }
      return { ok: true, value: parsed as Record<string, unknown> };
    } catch (err) {
      const detail = err instanceof Error ? err.message : "invalid JSON";
      return { ok: false, error: detail };
    }
  }, [buffer, t]);

  const dirty = buffer !== pristineJson;

  const handleEdit = useCallback(() => {
    setBuffer(pristineJson);
    setEditing(true);
  }, [pristineJson]);

  const handleCancelEdit = useCallback(() => {
    setBuffer(pristineJson);
    setEditing(false);
  }, [pristineJson]);

  const handleApprove = useCallback(async () => {
    setSubmitting(true);
    try {
      if (dirty) {
        if (!parseResult.ok) return;
        await resumeRun(threadId, runId, {
          decision: "modify",
          modified_args: parseResult.value,
        });
        message.success(t("approval_card.approved_with_edits"));
      } else {
        await resumeRun(threadId, runId, { decision: "approve" });
        message.success(t("approval_card.approved"));
      }
      setEditing(false);
      onResolved();
    } catch (err) {
      const msg = err instanceof ApiError ? `${err.code}: ${err.message}` : String(err);
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  }, [dirty, parseResult, threadId, runId, onResolved, t, message]);

  const handleReject = useCallback(async () => {
    setSubmitting(true);
    try {
      await resumeRun(threadId, runId, { decision: "reject" });
      message.success(t("approval_card.rejected"));
      onResolved();
    } catch (err) {
      const msg = err instanceof ApiError ? `${err.code}: ${err.message}` : String(err);
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  }, [threadId, runId, onResolved, t, message]);

  const approveLabel = dirty
    ? t("approval_card.approve_with_edits")
    : t("approval_card.approve");

  return (
    <Card
      data-testid="approval-card"
      style={{ marginBottom: 16 }}
      title={
        <Space size={8}>
          <AlertTriangle size={16} strokeWidth={1.75} />
          <Text strong>
            {approval.node} — {t("approval_card.awaiting_human")}
          </Text>
        </Space>
      }
    >
      <p style={{ margin: "0 0 8px", color: "var(--hx-text-secondary)" }}>
        {approval.action_summary}
      </p>

      {/* RT-6 Tier B — warn the reviewer the workspace changed since this was
          requested, BEFORE they approve (approve-then-swap-script). */}
      {approval.workspace_drift ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("approval_card.workspace_drift_title")}
          description={t("approval_card.workspace_drift_body")}
          data-testid="approval-workspace-drift"
        />
      ) : null}

      <Space
        size={16}
        wrap
        style={{ marginBottom: 12, fontSize: 12, color: "var(--hx-text-tertiary)" }}
      >
        <span>
          {t("approval_card.reason_kind")}:{" "}
          <Text code style={{ fontSize: 11 }}>
            {approval.reason_kind}
          </Text>
        </span>
        <span>
          {t("approval_card.requested_at")}:{" "}
          {new Date(approval.requested_at).toLocaleString()}
        </span>
        <span>
          {t("approval_card.timeout_at")}:{" "}
          {new Date(approval.timeout_at).toLocaleString()}
        </span>
        {/* RT-6 Tier A — the approved-args fingerprint receipt (short prefix). */}
        {approval.binding_digest ? (
          <Tooltip title={t("approval_card.binding_hint")}>
            <span>
              {t("approval_card.binding")}:{" "}
              <Text code style={{ fontSize: 11 }} data-testid="approval-binding-digest">
                {approval.binding_digest.slice(0, 12)}
              </Text>
            </span>
          </Tooltip>
        ) : null}
      </Space>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
        }}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          {editing
            ? t("approval_card.editing_hint")
            : t("approval_card.proposed_args_label")}
        </Text>
        {editing ? (
          <Button
            size="small"
            icon={<X size={12} strokeWidth={1.75} />}
            onClick={handleCancelEdit}
            disabled={submitting}
            data-testid="approval-cancel-edit"
          >
            {t("approval_card.cancel_edit")}
          </Button>
        ) : (
          <Button
            size="small"
            icon={<Edit3 size={12} strokeWidth={1.75} />}
            onClick={handleEdit}
            data-testid="approval-edit"
          >
            {t("approval_card.edit_arguments")}
          </Button>
        )}
      </div>

      <Editor
        language="json"
        value={editing ? buffer : pristineJson}
        onChange={(v) => setBuffer(v ?? "")}
        theme="vs-dark"
        height={240}
        options={{
          readOnly: !editing,
          minimap: { enabled: false },
          fontFamily: "var(--hx-font-mono)",
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
        }}
        data-testid="approval-editor"
      />

      {editing && !parseResult.ok && (
        <Alert
          type="error"
          showIcon
          message={t("approval_card.json_parse_error")}
          description={parseResult.error}
          style={{ marginTop: 8 }}
          data-testid="approval-json-error"
        />
      )}

      <Space style={{ marginTop: 12 }}>
        <Button
          type="primary"
          icon={<Check size={14} strokeWidth={1.75} />}
          loading={submitting}
          disabled={editing && !parseResult.ok}
          onClick={() => void handleApprove()}
          data-testid="approval-approve"
        >
          {approveLabel}
        </Button>
        <Button
          danger
          loading={submitting}
          onClick={() => void handleReject()}
          data-testid="approval-reject"
        >
          {t("approval_card.reject")}
        </Button>
      </Space>
    </Card>
  );
}
