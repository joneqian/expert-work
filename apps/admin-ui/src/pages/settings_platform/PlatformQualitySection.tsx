/**
 * Platform quality-monitor config section — Stream RT-5 (PR-3c, §14).
 *
 * Self-contained section: GETs the platform quality-config on mount and shows a
 * form to tune the production quality monitor (enable toggle / sampling / judge
 * model / drift thresholds). ``enabled`` is the operational on/off — since PR-3b
 * the workers always run and read this config live, so flipping it here takes
 * effect without a restart (previously an env var + restart). Leads with a
 * friendly explanation + a cost note (judge tokens). system_admin-only at the
 * route level; surfaces backend error codes.
 */
import { useCallback, useEffect, useState, type ReactElement } from "react";
import { Alert, App, Button, Form, Input, InputNumber, Spin, Switch, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  getPlatformQualityConfig,
  putPlatformQualityConfig,
  type QualityConfig,
} from "../../api/platform_quality_config";
import { ApiError } from "../../api/client";

const { Paragraph } = Typography;

interface NumberFieldProps {
  name: keyof QualityConfig;
  label: string;
  min: number;
  max?: number;
  step?: number;
}

function NumberField({ name, label, min, max, step }: NumberFieldProps): ReactElement {
  return (
    <Form.Item name={name} label={label} rules={[{ required: true }]}>
      <InputNumber min={min} max={max} step={step} style={{ width: 200 }} aria-label={label} />
    </Form.Item>
  );
}

function SectionTitle({ children }: { children: string }): ReactElement {
  return <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>{children}</h3>;
}

export function PlatformQualitySection(): ReactElement {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<QualityConfig>();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const view = await getPlatformQualityConfig();
      form.setFieldsValue(view.config);
      setIsDefault(view.is_default);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, [form]);

  useEffect(() => {
    void load();
  }, [load]);

  const errMessage = useCallback(
    (err: ApiError): string => {
      const key = `settings_platform.quality_err_${err.code}`;
      const translated = t(key);
      return translated === key ? err.message : translated;
    },
    [t],
  );

  const onFinish = useCallback(
    async (values: QualityConfig) => {
      setSaving(true);
      setSaveError(null);
      try {
        const result = await putPlatformQualityConfig(values);
        form.setFieldsValue(result.config);
        setIsDefault(false);
        message.success(t("settings_platform.quality_saved"));
      } catch (err) {
        setSaveError(
          err instanceof ApiError ? errMessage(err) : t("settings_platform.quality_save_error"),
        );
      } finally {
        setSaving(false);
      }
    },
    [form, message, t, errMessage],
  );

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }} data-testid="pq-loading">
        <Spin />
      </div>
    );
  }

  if (loadError !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("settings_platform.quality_help_title")}
        description={loadError}
        data-testid="pq-load-error"
      />
    );
  }

  return (
    <div data-testid="pq-root">
      <Alert
        type="info"
        showIcon
        message={t("settings_platform.quality_help_title")}
        description={t("settings_platform.quality_help_body")}
        style={{ marginBottom: 16 }}
        data-testid="pq-help"
      />
      {isDefault && (
        <Alert
          type="warning"
          showIcon
          message={t("settings_platform.quality_default_note")}
          style={{ marginBottom: 16 }}
          data-testid="pq-default-note"
        />
      )}

      <Form form={form} layout="vertical" onFinish={onFinish} style={{ maxWidth: 520 }}>
        <SectionTitle>{t("settings_platform.quality_section_master")}</SectionTitle>
        <Form.Item
          name="enabled"
          label={t("settings_platform.quality_enabled_label")}
          valuePropName="checked"
          extra={t("settings_platform.quality_enabled_hint")}
        >
          <Switch data-testid="pq-enabled" aria-label={t("settings_platform.quality_enabled_label")} />
        </Form.Item>

        <SectionTitle>{t("settings_platform.quality_section_sampling")}</SectionTitle>
        <NumberField
          name="sampling_rate_pct"
          label={t("settings_platform.quality_sampling_rate_label")}
          min={0}
          max={100}
        />
        <NumberField
          name="daily_cap"
          label={t("settings_platform.quality_daily_cap_label")}
          min={1}
        />
        <NumberField
          name="monitor_interval_s"
          label={t("settings_platform.quality_monitor_interval_label")}
          min={1}
        />
        <NumberField
          name="monitor_batch_size"
          label={t("settings_platform.quality_batch_size_label")}
          min={1}
        />

        <SectionTitle>{t("settings_platform.quality_section_judge")}</SectionTitle>
        <Form.Item
          name="judge_provider"
          label={t("settings_platform.quality_judge_provider_label")}
          extra={t("settings_platform.quality_judge_hint")}
          rules={[{ required: true }]}
        >
          <Input
            style={{ width: 260 }}
            aria-label={t("settings_platform.quality_judge_provider_label")}
          />
        </Form.Item>
        <Form.Item
          name="judge_model"
          label={t("settings_platform.quality_judge_model_label")}
          rules={[{ required: true }]}
        >
          <Input
            style={{ width: 260 }}
            aria-label={t("settings_platform.quality_judge_model_label")}
          />
        </Form.Item>

        <SectionTitle>{t("settings_platform.quality_section_drift")}</SectionTitle>
        <NumberField
          name="drift_interval_s"
          label={t("settings_platform.quality_drift_interval_label")}
          min={1}
        />
        <NumberField
          name="drift_recent_window_h"
          label={t("settings_platform.quality_recent_window_label")}
          min={1}
        />
        <NumberField
          name="drift_baseline_window_h"
          label={t("settings_platform.quality_baseline_window_label")}
          min={1}
        />
        <NumberField
          name="drift_min_samples"
          label={t("settings_platform.quality_min_samples_label")}
          min={1}
        />
        <NumberField
          name="drift_threshold"
          label={t("settings_platform.quality_threshold_label")}
          min={0.01}
          max={1}
          step={0.01}
        />
        <NumberField
          name="drift_cooldown_h"
          label={t("settings_platform.quality_cooldown_label")}
          min={1}
        />

        {saveError !== null && (
          <Alert
            type="error"
            showIcon
            message={saveError}
            style={{ marginBottom: 16 }}
            data-testid="pq-error"
          />
        )}

        <Form.Item>
          <Button type="primary" htmlType="submit" loading={saving} data-testid="pq-save">
            {t("settings_platform.quality_save")}
          </Button>
        </Form.Item>
      </Form>

      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t("settings_platform.quality_deploy_note")}
      </Paragraph>
    </div>
  );
}
