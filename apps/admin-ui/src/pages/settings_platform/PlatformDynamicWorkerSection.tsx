/**
 * Platform dynamic-worker guardrail section (B3 PR2).
 *
 * Self-contained section: GETs the platform dynamic-worker limits on mount
 * and shows three number inputs for the resolved (effective) guardrails —
 * per-run concurrency, per-run cumulative spawn cap, and per-worker step
 * cap. Saving writes an explicit platform override that takes effect on the
 * next run/build — no redeploy, overriding the process's env-default
 * settings snapshot. system_admin-only at the route level; surfaces backend
 * errors.
 */
import { useCallback, useEffect, useState, type ReactElement } from "react";
import { Alert, App, Button, InputNumber, Space, Spin, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  getPlatformDynamicWorkerConfig,
  putPlatformDynamicWorkerConfig,
  type DynamicWorkerLimits,
  type PlatformDynamicWorkerConfigView,
} from "../../api/platform_dynamic_worker_config";
import { ApiError } from "../../api/client";

const { Paragraph } = Typography;

export interface PlatformDynamicWorkerSectionProps {
  /** Invoked after a successful save (so a parent page can refresh/notify). */
  onSaved?: () => void;
}

export function PlatformDynamicWorkerSection({
  onSaved,
}: PlatformDynamicWorkerSectionProps): ReactElement {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [view, setView] = useState<PlatformDynamicWorkerConfigView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // ``null`` while the field is transiently empty during editing (e.g. the
  // user has cleared it but hasn't typed a new digit yet) — coercing to a
  // fallback number immediately would fight the user's keystrokes.
  const [maxConcurrent, setMaxConcurrent] = useState<number | null>(1);
  const [maxPerRun, setMaxPerRun] = useState<number | null>(1);
  const [maxIterations, setMaxIterations] = useState<number | null>(1);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const next = await getPlatformDynamicWorkerConfig();
      setView(next);
      setMaxConcurrent(next.effective.max_concurrent);
      setMaxPerRun(next.effective.max_per_run);
      setMaxIterations(next.effective.max_iterations);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const hasEmptyField =
    maxConcurrent === null || maxPerRun === null || maxIterations === null;

  const onSave = useCallback(async () => {
    if (maxConcurrent === null || maxPerRun === null || maxIterations === null) {
      return;
    }
    const limits: DynamicWorkerLimits = {
      max_concurrent: maxConcurrent,
      max_per_run: maxPerRun,
      max_iterations: maxIterations,
    };
    setSaving(true);
    try {
      setView(await putPlatformDynamicWorkerConfig(limits));
      message.success(t("settings_platform.dynamic_worker_saved"));
      onSaved?.();
    } catch (err) {
      message.error(
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : t("settings_platform.dynamic_worker_save_failed"),
      );
    } finally {
      setSaving(false);
    }
  }, [maxConcurrent, maxPerRun, maxIterations, message, t, onSaved]);

  if (loading) {
    return (
      <div
        style={{ padding: 24, textAlign: "center" }}
        data-testid="pdw-loading"
      >
        <Spin />
      </div>
    );
  }

  if (loadError !== null || view === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("settings_platform.dynamic_worker_heading")}
        description={loadError ?? "unknown error"}
        data-testid="pdw-load-error"
      />
    );
  }

  return (
    <div data-testid="pdw-root">
      <Alert
        type="info"
        showIcon
        message={t("settings_platform.dynamic_worker_help_title")}
        description={t("settings_platform.dynamic_worker_help_body")}
        style={{ marginBottom: 16 }}
        data-testid="pdw-help"
      />

      <Space direction="vertical" size={12}>
        <Space align="center">
          <span>{t("settings_platform.dynamic_worker_max_concurrent_label")}</span>
          <InputNumber
            min={1}
            max={16}
            value={maxConcurrent}
            onChange={setMaxConcurrent}
            aria-label={t("settings_platform.dynamic_worker_max_concurrent_label")}
            data-testid="pdw-max-concurrent"
          />
        </Space>
        <Space align="center">
          <span>{t("settings_platform.dynamic_worker_max_per_run_label")}</span>
          <InputNumber
            min={1}
            max={256}
            value={maxPerRun}
            onChange={setMaxPerRun}
            aria-label={t("settings_platform.dynamic_worker_max_per_run_label")}
            data-testid="pdw-max-per-run"
          />
        </Space>
        <Space align="center">
          <span>{t("settings_platform.dynamic_worker_max_iterations_label")}</span>
          <InputNumber
            min={1}
            max={64}
            value={maxIterations}
            onChange={setMaxIterations}
            aria-label={t("settings_platform.dynamic_worker_max_iterations_label")}
            data-testid="pdw-max-iterations"
          />
        </Space>
        {view.configured === null && (
          <Tag data-testid="pdw-env-default">
            {t("settings_platform.dynamic_worker_env_default")}
          </Tag>
        )}
      </Space>

      <div style={{ marginTop: 16 }}>
        <Button
          type="primary"
          loading={saving}
          disabled={hasEmptyField}
          onClick={onSave}
          data-testid="pdw-save"
        >
          {t("settings_platform.dynamic_worker_save")}
        </Button>
      </div>

      <Paragraph
        type="secondary"
        style={{ marginTop: 8 }}
        data-testid="pdw-hint"
      >
        {t("settings_platform.dynamic_worker_hint")}
      </Paragraph>
    </div>
  );
}
