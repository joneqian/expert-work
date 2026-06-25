/**
 * Create-platform-server Modal — Stream MCP platform-servers.
 *
 * Hosts the shared {@link CatalogConfigForm} (create mode) in a dialog. The
 * Modal's OK button triggers the form's imperative ``submit()``; the form
 * connect-probes the server and surfaces errors inline, closing only on success.
 * Editing happens on a dedicated page (``McpCatalogDetail``), not here.
 */
import { useRef, useState } from "react";
import { Modal } from "antd";
import { useTranslation } from "react-i18next";

import {
  CatalogConfigForm,
  type CatalogConfigFormHandle,
} from "./CatalogConfigForm";

export interface CatalogCreateModalProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function CatalogCreateModal({
  open,
  onClose,
  onSaved,
}: CatalogCreateModalProps) {
  const { t } = useTranslation();
  const formRef = useRef<CatalogConfigFormHandle>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <Modal
      open={open}
      onCancel={onClose}
      title={t("mcp_catalog.add_title")}
      width={600}
      okText={t("mcp_catalog.submit_add")}
      confirmLoading={submitting}
      onOk={() => void formRef.current?.submit()}
      data-testid="cce-modal"
    >
      {open && (
        <CatalogConfigForm
          ref={formRef}
          editing={null}
          onSaved={() => {
            onSaved();
            onClose();
          }}
          onSubmittingChange={setSubmitting}
        />
      )}
    </Modal>
  );
}
