/**
 * Manage platform skill drawer — Stream X (system_admin).
 *
 * Opened from a row's "Manage" action on ``/settings/platform-skills``.
 * Surfaces the full per-skill lifecycle:
 *
 *   - version history (``GET .../versions``);
 *   - add-version form (``POST .../versions``) — prompt fragment + tool
 *     names + required models + description. The description nudges the
 *     author to describe *when* to use the skill, because platform-skill
 *     selection is model-driven on that description (research R4);
 *   - status Select (draft / active / archived → ``PATCH``);
 *   - pin / unpin toggle (``PATCH``).
 *
 * Moderation/threat 400s on add-version surface as a toast. After any
 * mutation the parent list is refreshed via ``onChanged``.
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import {
  addPlatformSkillVersion,
  listPlatformSkillVersions,
  patchPlatformSkill,
  type AddPlatformSkillVersionBody,
  type PlatformSkill,
  type PlatformSkillStatus,
  type PlatformSkillVersion,
} from "../../api/platform-skills";
import { ApiError } from "../../api/client";

const { Text } = Typography;

const STATUS_OPTIONS: PlatformSkillStatus[] = ["draft", "active", "archived"];

export interface PlatformSkillManageDrawerProps {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
  skill: PlatformSkill | null;
}

interface AddVersionForm {
  prompt_fragment: string;
  tool_names?: string;
  required_models?: string;
  description?: string;
}

function splitCsv(value: string | undefined): string[] {
  if (value === undefined) return [];
  return value
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function errText(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}

export function PlatformSkillManageDrawer({
  open,
  onClose,
  onChanged,
  skill,
}: PlatformSkillManageDrawerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<AddVersionForm>();

  const [versions, setVersions] = useState<PlatformSkillVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  // Bumped after a successful add-version to force a reload. Status/pin
  // PATCHes mutate the parent ``skill`` object ref but don't touch
  // versions, so the version-loading effect keys on ``skill?.id`` (the
  // skill identity) rather than the whole object — avoiding a wasted
  // reload on every parent refresh.
  const [versionsReloadKey, setVersionsReloadKey] = useState(0);
  const [adding, setAdding] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);
  const [pinSaving, setPinSaving] = useState(false);

  const loadVersions = useCallback(
    async (skillId: string) => {
      setVersionsLoading(true);
      try {
        const result = await listPlatformSkillVersions(skillId);
        setVersions(result.items);
      } catch (err) {
        message.error(errText(err));
      } finally {
        setVersionsLoading(false);
      }
    },
    [message],
  );

  const skillId = skill?.id;

  useEffect(() => {
    if (open && skillId !== undefined) {
      void loadVersions(skillId);
    } else {
      setVersions([]);
      form.resetFields();
    }
  }, [open, skillId, versionsReloadKey, loadVersions, form]);

  const onAddVersion = useCallback(async () => {
    if (!skill) return;
    let values: AddVersionForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const body: AddPlatformSkillVersionBody = {
      prompt_fragment: values.prompt_fragment,
      tool_names: splitCsv(values.tool_names),
      required_models: splitCsv(values.required_models),
      description: values.description ?? "",
    };
    setAdding(true);
    try {
      const created = await addPlatformSkillVersion(skill.id, body);
      message.success(t("platform_skills.version_added", { version: created.version }));
      form.resetFields();
      setVersionsReloadKey((k) => k + 1);
      onChanged();
    } catch (err) {
      message.error(errText(err));
    } finally {
      setAdding(false);
    }
  }, [skill, form, message, t, onChanged]);

  const onStatusChange = useCallback(
    async (status: PlatformSkillStatus) => {
      if (!skill) return;
      setStatusSaving(true);
      try {
        await patchPlatformSkill(skill.id, { status });
        message.success(t("platform_skills.status_changed", { status }));
        onChanged();
      } catch (err) {
        message.error(errText(err));
      } finally {
        setStatusSaving(false);
      }
    },
    [skill, message, t, onChanged],
  );

  const onPinToggle = useCallback(
    async (pinned: boolean) => {
      if (!skill) return;
      setPinSaving(true);
      try {
        await patchPlatformSkill(skill.id, { pinned });
        message.success(
          pinned ? t("platform_skills.pinned") : t("platform_skills.unpinned"),
        );
        onChanged();
      } catch (err) {
        message.error(errText(err));
      } finally {
        setPinSaving(false);
      }
    },
    [skill, message, t, onChanged],
  );

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={skill ? t("platform_skills.manage_title", { name: skill.name }) : ""}
      width={620}
      destroyOnHidden
      data-testid="ps-manage-drawer"
    >
      {skill && (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          {/* Lifecycle controls */}
          <div>
            <Text strong>{t("platform_skills.lifecycle_title")}</Text>
            <div style={{ display: "flex", gap: 16, alignItems: "center", marginTop: 8 }}>
              <Space size={6}>
                <Text type="secondary">{t("platform_skills.col_status")}</Text>
                <Select<PlatformSkillStatus>
                  value={skill.status}
                  style={{ width: 160 }}
                  loading={statusSaving}
                  onChange={onStatusChange}
                  aria-label={t("platform_skills.col_status")}
                  data-testid="psm-status"
                  options={STATUS_OPTIONS.map((s) => ({
                    value: s,
                    label: t(`platform_skills.status_${s}`),
                  }))}
                />
              </Space>
              <Space size={6}>
                <Text type="secondary">{t("platform_skills.pinned")}</Text>
                <Switch
                  size="small"
                  checked={skill.pinned}
                  loading={pinSaving}
                  onChange={onPinToggle}
                  aria-label={t("platform_skills.pinned")}
                  data-testid="psm-pin"
                />
              </Space>
            </div>
          </div>

          {/* Add version */}
          <div>
            <Text strong>{t("platform_skills.add_version_title")}</Text>
            <Form
              form={form}
              layout="vertical"
              style={{ marginTop: 8 }}
              data-testid="psm-add-version-form"
            >
              <Form.Item
                name="prompt_fragment"
                label={t("platform_skills.field_prompt_fragment")}
                rules={[
                  {
                    required: true,
                    message: t("platform_skills.prompt_fragment_required"),
                  },
                ]}
              >
                <Input.TextArea data-testid="psm-prompt-fragment" rows={4} />
              </Form.Item>
              <Form.Item
                name="description"
                label={t("platform_skills.field_version_description")}
                extra={t("platform_skills.when_to_use_hint")}
              >
                <Input.TextArea
                  data-testid="psm-description"
                  rows={2}
                  placeholder={t("platform_skills.when_to_use_hint")}
                />
              </Form.Item>
              <Form.Item
                name="tool_names"
                label={t("platform_skills.field_tool_names")}
                extra={t("platform_skills.csv_hint")}
              >
                <Input data-testid="psm-tool-names" placeholder="web_search, http" />
              </Form.Item>
              <Form.Item
                name="required_models"
                label={t("platform_skills.field_required_models")}
                extra={t("platform_skills.csv_hint")}
              >
                <Input data-testid="psm-required-models" placeholder="gpt-4o, claude-3-5" />
              </Form.Item>
              <Button
                type="primary"
                loading={adding}
                onClick={onAddVersion}
                data-testid="psm-add-version"
              >
                {t("platform_skills.add_version_submit")}
              </Button>
            </Form>
          </div>

          {/* Version history */}
          <div>
            <Text strong>{t("platform_skills.versions_title")}</Text>
            <List<PlatformSkillVersion>
              style={{ marginTop: 8 }}
              loading={versionsLoading}
              dataSource={versions}
              data-testid="psm-versions"
              locale={{
                emptyText: <Empty description={t("platform_skills.no_versions")} />,
              }}
              renderItem={(v) => (
                <List.Item data-testid={`psm-version-${v.version}`}>
                  <List.Item.Meta
                    title={
                      <Space size={6}>
                        <Tag bordered={false}>v{v.version}</Tag>
                        {v.high_risk && (
                          <Tag color="warning">
                            {t("platform_skills.high_risk")}
                          </Tag>
                        )}
                        {v.lazy_load && <Tag>{t("platform_skills.lazy")}</Tag>}
                      </Space>
                    }
                    description={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {v.description || "—"}
                      </Text>
                    }
                  />
                </List.Item>
              )}
            />
          </div>
        </Space>
      )}
    </Drawer>
  );
}
