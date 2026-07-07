/**
 * Platform tool-output-budget section (Phase 3).
 *
 * Self-contained section: GETs the platform tool-budget master switch on mount
 * and shows a toggle for the resolved on/off. Off ⇒ the whole tool-output-budget
 * feature (generalized externalization + persist floor + CM-12 prune) is reverted
 * platform-wide on the next agent build — no redeploy, overriding the
 * ``EXPERT_WORK_TOOL_OUTPUT_BUDGET`` env default. ``effective = platform AND agent``,
 * so this is a master kill: per-agent flags can only narrow it further.
 * system_admin-only at the route level; surfaces backend errors.
 */
import { useCallback, useEffect, useState, type ReactElement } from "react";
import { Alert, App, Spin, Switch, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  getPlatformToolBudgetConfig,
  putPlatformToolBudgetConfig,
  type PlatformToolBudgetConfigView,
} from "../../api/platform_tool_budget_config";
import { ApiError } from "../../api/client";

const { Paragraph, Text } = Typography;

export interface PlatformToolBudgetSectionProps {
  /** Invoked after a successful save (so a parent page can refresh/notify). */
  onSaved?: () => void;
}

export function PlatformToolBudgetSection({
  onSaved,
}: PlatformToolBudgetSectionProps): ReactElement {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [view, setView] = useState<PlatformToolBudgetConfigView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setView(await getPlatformToolBudgetConfig());
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
        setView(await putPlatformToolBudgetConfig(next));
        message.success(t("settings_platform.tool_budget_saved"));
        onSaved?.();
      } catch (err) {
        message.error(
          err instanceof ApiError
            ? `${err.code}: ${err.message}`
            : t("settings_platform.tool_budget_save_failed"),
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
        data-testid="ptb-loading"
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
        message={t("settings_platform.tool_budget_title")}
        description={loadError ?? "unknown error"}
        data-testid="ptb-load-error"
      />
    );
  }

  return (
    <div data-testid="ptb-root">
      <Alert
        type="info"
        showIcon
        message={t("settings_platform.tool_budget_help_title")}
        description={t("settings_platform.tool_budget_help_body")}
        style={{ marginBottom: 16 }}
        data-testid="ptb-help"
      />

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Switch
          checked={view.effective}
          loading={saving}
          onChange={onToggle}
          aria-label={t("settings_platform.tool_budget_toggle_label")}
          data-testid="ptb-toggle"
        />
        <Text strong>
          {view.effective
            ? t("settings_platform.tool_budget_enabled")
            : t("settings_platform.tool_budget_disabled")}
        </Text>
        {view.enabled === null && (
          <Tag data-testid="ptb-env-default">
            {t("settings_platform.tool_budget_env_default")}
          </Tag>
        )}
      </div>
      <Paragraph
        type="secondary"
        style={{ marginTop: 8 }}
        data-testid="ptb-hint"
      >
        {t("settings_platform.tool_budget_hint")}
      </Paragraph>
    </div>
  );
}
