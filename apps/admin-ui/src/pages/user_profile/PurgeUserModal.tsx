/**
 * PurgeUserModal — the high-risk, type-to-confirm gate for
 * ``POST /v1/users/{id}:purge`` (Phase 3a).
 *
 * Irreversibly cascade-purges an external end-user's data + assets. To arm the
 * danger button the admin must type the user's ``subject_id`` verbatim. An
 * employee (console member) is rejected 409 by the backend — surfaced as a hint
 * to use the members page instead.
 */
import { useState } from "react";
import { Alert, App, Input, Modal, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { ApiError } from "../../api/client";
import { purgeUser } from "../../api/users";
import { errMessage } from "./useLoad";

const { Paragraph, Text } = Typography;

interface PurgeUserModalProps {
  open: boolean;
  onClose: () => void;
  userId: string;
  /** The value the admin must type to arm the delete (the passed-in user id). */
  subjectId: string;
  displayName?: string;
  /** Called after a successful purge — the caller navigates away. */
  onPurged: () => void;
}

export function PurgeUserModal({
  open,
  onClose,
  userId,
  subjectId,
  displayName,
  onPurged,
}: PurgeUserModalProps) {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);

  // Armed only on an exact, non-empty match — never arm on an empty subjectId.
  const armed = subjectId.length > 0 && confirmText.trim() === subjectId;

  const close = () => {
    if (busy) return;
    setConfirmText("");
    onClose();
  };

  const onConfirm = async () => {
    if (!armed || busy) return;
    setBusy(true);
    try {
      const summary = await purgeUser(userId);
      // Best-effort: the endpoint returns 200 even when some steps failed.
      if (!summary.ok) {
        // Partial purge — stay on the page so the "retry" hint is actionable
        // (re-purge is idempotent). Keep the input armed for a one-click retry.
        message.warning(t("user_profile.purge_partial"));
        setBusy(false);
        return;
      }
      message.success(t("user_profile.purge_done"));
      setConfirmText("");
      onPurged();
      // Intentionally leave `busy` set: the parent unmounts this modal on
      // navigation, and resetting it here would briefly re-arm the button
      // (a fast second Enter could otherwise re-fire the purge).
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Employee (console member) — must be purged from the members page.
        modal.warning({
          title: t("user_profile.purge_employee_title"),
          content: t("user_profile.purge_employee_body"),
        });
      } else {
        modal.error({
          title: t("user_profile.purge_failed_title"),
          content: errMessage(err),
        });
      }
      setBusy(false); // a failed purge is retryable — re-enable the button
    }
  };

  const who = displayName ? `${subjectId} (${displayName})` : subjectId;

  return (
    <Modal
      open={open}
      onCancel={close}
      onOk={onConfirm}
      okText={t("user_profile.purge_confirm_btn")}
      cancelText={t("common.cancel")}
      okButtonProps={{ danger: true, disabled: !armed, loading: busy, "data-testid": "purge-confirm-ok" }}
      title={t("user_profile.purge_title")}
      destroyOnClose
      data-testid="purge-user-modal"
    >
      <Alert
        type="error"
        showIcon
        message={t("user_profile.purge_warning", { who })}
        description={
          <>
            <Paragraph style={{ marginBottom: 4 }}>{t("user_profile.purge_deletes")}</Paragraph>
            <Paragraph style={{ marginBottom: 4 }} type="secondary">
              {t("user_profile.purge_anonymizes")}
            </Paragraph>
            <Paragraph style={{ marginBottom: 0 }} type="secondary">
              {t("user_profile.purge_archive_note")}
            </Paragraph>
          </>
        }
        style={{ marginBottom: 16 }}
      />
      <Paragraph style={{ marginBottom: 6 }}>
        {t("user_profile.purge_type_to_confirm")}{" "}
        <Text code>{subjectId}</Text>
      </Paragraph>
      <Input
        value={confirmText}
        onChange={(e) => setConfirmText(e.target.value)}
        onPressEnter={onConfirm}
        placeholder={subjectId}
        disabled={busy}
        data-testid="purge-confirm-input"
      />
    </Modal>
  );
}
