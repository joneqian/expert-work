/**
 * Create-template Modal — Stream Agent-Templates (M1-6). Hosts the shared
 * ``AgentTemplateConfigForm`` in create mode; the Modal OK drives its imperative
 * ``submit()``. Mirrors ``CatalogCreateModal``.
 */
import { useRef, useState } from "react";
import { Modal } from "antd";
import { useTranslation } from "react-i18next";

import {
  AgentTemplateConfigForm,
  type AgentTemplateConfigFormHandle,
} from "./AgentTemplateConfigForm";

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function AgentTemplateCreateModal({ open, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const formRef = useRef<AgentTemplateConfigFormHandle>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <Modal
      open={open}
      title={t("agent_templates.create_title")}
      width={900}
      okText={t("agent_templates.create_btn")}
      cancelText={t("common.cancel")}
      confirmLoading={submitting}
      onOk={() => void formRef.current?.submit()}
      onCancel={onClose}
      destroyOnHidden
      data-testid="atcf-create-modal"
    >
      {open && (
        <AgentTemplateConfigForm
          ref={formRef}
          editing={null}
          onSubmittingChange={setSubmitting}
          onSaved={() => {
            onSaved();
            onClose();
          }}
        />
      )}
    </Modal>
  );
}
