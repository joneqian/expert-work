/**
 * Create platform skill drawer — Stream X (system_admin).
 *
 * Opened from ``/settings/platform-skills``. Submits
 * ``POST /v1/platform/skills`` to create an empty draft skill; versions
 * are added afterwards via the Manage drawer. A 409 surfaces a friendly
 * "name already exists" message.
 *
 * Mirrors ``CatalogEntryDrawer`` (Drawer, ``Form.useForm``,
 * ``layout="vertical"``, footer Cancel / Submit, ``ApiError`` →
 * ``${err.code}: ${err.message}`` message, reset-on-close).
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Drawer, Form, Input, Select } from "antd";
import { useTranslation } from "react-i18next";

import {
  createPlatformSkill,
  type CreatePlatformSkillBody,
  type PlatformSkillTier,
} from "../../api/platform-skills";
import { ApiError } from "../../api/client";

interface CreateForm {
  name: string;
  description?: string;
  category?: string;
  required_tier: PlatformSkillTier;
}

export interface PlatformSkillCreateDrawerProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const TIER_OPTIONS: { value: PlatformSkillTier; labelKey: string }[] = [
  { value: "free", labelKey: "platform_skills.tier_free" },
  { value: "pro", labelKey: "platform_skills.tier_pro" },
  { value: "enterprise", labelKey: "platform_skills.tier_enterprise" },
];

const NAME_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

export function PlatformSkillCreateDrawer({
  open,
  onClose,
  onCreated,
}: PlatformSkillCreateDrawerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<CreateForm>();
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      form.resetFields();
      return;
    }
    form.setFieldsValue({ required_tier: "free" });
  }, [open, form]);

  const handleCancel = useCallback(() => {
    form.resetFields();
    onClose();
  }, [form, onClose]);

  const handleSubmit = useCallback(async () => {
    let values: CreateForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body: CreatePlatformSkillBody = {
      name: values.name,
      description: values.description ?? "",
      category: values.category ?? "",
      required_tier: values.required_tier,
    };
    setSubmitting(true);
    try {
      await createPlatformSkill(body);
      message.success(t("platform_skills.created"));
      // ``onCreated`` already closes the drawer (flips ``open`` false),
      // which triggers the open-effect's ``form.resetFields()`` — no
      // need to reset / close again here.
      onCreated();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        message.error(t("platform_skills.duplicate_name"));
        return;
      }
      const msg =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : err instanceof Error
            ? err.message
            : "unknown error";
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  }, [form, message, onCreated, t]);

  return (
    <Drawer
      open={open}
      onClose={handleCancel}
      title={t("platform_skills.create_title")}
      width={520}
      destroyOnHidden
      data-testid="ps-create-drawer"
      footer={
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={handleCancel} disabled={submitting} data-testid="psc-cancel">
            {t("common.cancel")}
          </Button>
          <Button
            type="primary"
            loading={submitting}
            onClick={handleSubmit}
            data-testid="psc-submit"
          >
            {t("platform_skills.create_submit")}
          </Button>
        </div>
      }
    >
      <Form form={form} layout="vertical" data-testid="psc-form">
        <Form.Item
          name="name"
          label={t("platform_skills.field_name")}
          extra={t("platform_skills.field_name_hint")}
          rules={[
            { required: true, message: t("platform_skills.name_required") },
            { pattern: NAME_PATTERN, message: t("platform_skills.name_required") },
          ]}
        >
          <Input data-testid="psc-name" maxLength={64} placeholder="web_search" />
        </Form.Item>

        <Form.Item name="category" label={t("platform_skills.field_category")}>
          <Input data-testid="psc-category" maxLength={64} placeholder="web" />
        </Form.Item>

        <Form.Item name="description" label={t("platform_skills.field_description")}>
          <Input.TextArea
            data-testid="psc-description"
            maxLength={512}
            rows={3}
            placeholder={t("platform_skills.when_to_use_hint")}
          />
        </Form.Item>

        <Form.Item name="required_tier" label={t("platform_skills.field_required_tier")}>
          <Select<PlatformSkillTier>
            data-testid="psc-tier"
            options={TIER_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
