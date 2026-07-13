/**
 * RunStatusBanner — Task 7. Shared "快速定位问题" top-of-view status banner,
 * mounted atop both the trace view (Task 10) and the timeline view
 * (Task 11); each caller computes its own status/summary/metrics and passes
 * them in — this component is a dumb presentational shell. Transcribes the
 * wireframe's `.banner.ok` / `.banner.err` — see
 * docs/superpowers/specs/2026-07-13-debug-console-clarity-wireframe.html
 * section 1 (and 2026-07-13-debug-console-full-page.html for the same
 * markup driven by state).
 */
import { useTranslation } from "react-i18next";

const SUCCESS = "var(--ew-text-success, #3ecf8e)";
const DANGER = "var(--ew-text-danger, #f0616d)";
const MUTED = "var(--ew-text-tertiary)";

export interface RunStatusBannerProps {
  status: "ok" | "error";
  /** Caller-built ok-state summary line, e.g. "运行成功 · 6 步 · …". */
  summary: string;
  /** ok-state right-aligned mono chips (耗时/tokens/$…). */
  metrics?: { label: string; value: string }[];
  /** error-state: the failing node's label. */
  errorLabel?: string;
  /** error-state: the failing node's status_message. */
  errorMessage?: string;
  /** error-state: jump to the error node. Button renders only when set. */
  onJump?: () => void;
}

export function RunStatusBanner({
  status,
  summary,
  metrics,
  errorLabel,
  errorMessage,
  onJump,
}: RunStatusBannerProps) {
  const { t } = useTranslation();
  const tone = status === "ok" ? SUCCESS : DANGER;

  return (
    <div
      data-testid="run-status-banner"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 11,
        padding: "9px 13px",
        margin: "8px 0 14px",
        borderRadius: 6,
        fontSize: 13,
        flexWrap: "wrap",
        border: `1px solid color-mix(in srgb, ${tone} ${status === "ok" ? 35 : 42}%, var(--ew-border-subtle))`,
        background: `color-mix(in srgb, ${tone} ${status === "ok" ? 9 : 10}%, var(--ew-surface-base))`,
      }}
    >
      <span aria-hidden style={{ width: 9, height: 9, borderRadius: "50%", flex: "0 0 auto", background: tone }} />

      {status === "ok" ? (
        <span style={{ color: "var(--ew-text-primary)" }}>{summary}</span>
      ) : (
        <span style={{ color: "var(--ew-text-primary)" }}>
          {t("playground.rb_failed_at", { label: errorLabel })}
          {errorMessage && (
            <>
              {" — "}
              <b style={{ fontWeight: 600 }}>{errorMessage}</b>
            </>
          )}
        </span>
      )}

      <span style={{ flex: 1 }} />

      {status === "ok" &&
        metrics?.map((m) => (
          <span
            key={m.label}
            style={{
              fontFamily: "var(--ew-font-mono)",
              fontVariantNumeric: "tabular-nums",
              fontSize: 12,
              color: MUTED,
            }}
          >
            {m.label} <b style={{ color: "var(--ew-text-primary)", fontWeight: 600 }}>{m.value}</b>
          </span>
        ))}

      {status === "error" && onJump && (
        <button
          type="button"
          data-testid="run-status-jump"
          onClick={onJump}
          style={{
            font: "inherit",
            fontSize: 12,
            color: DANGER,
            border: `1px solid color-mix(in srgb, ${DANGER} 45%, var(--ew-border-subtle))`,
            background: "transparent",
            padding: "3px 10px",
            borderRadius: 5,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {t("playground.rb_jump")}
        </button>
      )}
    </div>
  );
}
