/**
 * Platform billing-config section (Stream 12.4).
 *
 * Self-contained section: GETs the platform billing toggle on mount and shows a
 * switch for ``rollup_enabled``. The offline billing-rollup job (scheduled by a
 * k8s CronJob) reads this flag before each run and skips when off — so an
 * operator can pause cost rollup here without touching the cron. system_admin-
 * only at the route level; surfaces backend errors.
 */
import { useCallback, useEffect, useState, type ReactElement } from "react";
import { Alert, App, Spin, Switch, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  getPlatformBillingConfig,
  putPlatformBillingConfig,
} from "../../api/platform_billing_config";
import { ApiError } from "../../api/client";

const { Paragraph, Text } = Typography;

export interface PlatformBillingSectionProps {
  /** Invoked after a successful save (so a parent page can refresh/notify). */
  onSaved?: () => void;
}

export function PlatformBillingSection({
  onSaved,
}: PlatformBillingSectionProps): ReactElement {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const view = await getPlatformBillingConfig();
      setEnabled(view.rollup_enabled);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onToggle = useCallback(
    async (next: boolean) => {
      setSaving(true);
      try {
        const view = await putPlatformBillingConfig(next);
        setEnabled(view.rollup_enabled);
        message.success(t("settings_platform.billing_saved"));
        onSaved?.();
      } catch (err) {
        message.error(
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : t("settings_platform.billing_save_failed"),
        );
      } finally {
        setSaving(false);
      }
    },
    [message, t, onSaved],
  );

  if (loading) {
    return (
      <div
        style={{ padding: 24, textAlign: "center" }}
        data-testid="pb-loading"
      >
        <Spin />
      </div>
    );
  }

  if (loadError !== null || enabled === null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("settings_platform.billing_title")}
        description={loadError ?? "unknown error"}
        data-testid="pb-load-error"
      />
    );
  }

  return (
    <div data-testid="pb-root">
      <Alert
        type="info"
        showIcon
        message={t("settings_platform.billing_help_title")}
        description={t("settings_platform.billing_help_body")}
        style={{ marginBottom: 16 }}
        data-testid="pb-help"
      />

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Switch
          checked={enabled}
          loading={saving}
          onChange={onToggle}
          data-testid="pb-toggle"
        />
        <Text strong>
          {enabled
            ? t("settings_platform.billing_enabled")
            : t("settings_platform.billing_disabled")}
        </Text>
      </div>
      <Paragraph
        type="secondary"
        style={{ marginTop: 8 }}
        data-testid="pb-hint"
      >
        {t("settings_platform.billing_hint")}
      </Paragraph>
    </div>
  );
}
