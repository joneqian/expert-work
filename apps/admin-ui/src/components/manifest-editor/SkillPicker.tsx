/**
 * Skill picker — Tier 2 capability, its own form tab. Unlike a bare name
 * dropdown this surfaces the metadata an author needs to decide *whether* to
 * attach a skill: description, category, source (platform vs tenant) and the
 * plan-tier lock on platform skills the tenant is not entitled to. Tenant +
 * platform skills are merged (server-side name-shadowing already applied);
 * locked platform skills are shown but cannot be checked.
 *
 * Selected names that no longer resolve to a listed skill (e.g. hand-added in
 * raw YAML) are preserved as checked rows so a Form round-trip never drops
 * them. Emits the FULL merged manifest via the form_model writers.
 */
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Checkbox, Empty, Input, Tag, Tooltip, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { listSkills, type SkillRecord } from "../../api/skills";
import { FieldHelp } from "../FieldHelp";
import { readSkills, setSkills } from "./form_model";

const { Text } = Typography;

const SECTION: CSSProperties = { marginBottom: 24 };

function Heading({ children }: { children: ReactNode }) {
  return <h3 style={{ fontSize: 15, margin: "0 0 12px" }}>{children}</h3>;
}

/** The fields the picker renders per skill — a synthetic option built from a
 *  ``SkillRecord`` or, for an unresolved selected name, a name-only stub. */
interface SkillOption {
  name: string;
  description?: string;
  category?: string;
  source?: "tenant" | "platform";
  /** ``false`` only for platform skills the tenant's tier cannot use. */
  locked: boolean;
  requiredTier?: string;
}

function toOption(rec: SkillRecord): SkillOption {
  return {
    name: rec.name,
    description: rec.description,
    category: rec.category,
    source: rec.source ?? "tenant",
    locked: rec.source === "platform" && rec.entitled === false,
    requiredTier: rec.required_tier,
  };
}

interface SkillPickerProps {
  formData: unknown;
  onChange: (data: unknown) => void;
}

export function SkillPicker({ formData, onChange }: SkillPickerProps) {
  const { t } = useTranslation();
  const [skills, setSkillRecords] = useState<SkillRecord[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    listSkills().then(
      (s) => {
        if (!alive) return;
        setSkillRecords([...(s?.items ?? []), ...(s?.platform_items ?? [])]);
      },
      () => {},
    );
    return () => {
      alive = false;
    };
  }, []);

  const selected = readSkills(formData);

  // Merge listed skills with any selected name that didn't resolve to one, so
  // hand-authored refs survive and stay visibly checked.
  const options = useMemo<SkillOption[]>(() => {
    const byName = new Map<string, SkillOption>();
    for (const rec of skills) byName.set(rec.name, toOption(rec));
    for (const name of selected) {
      if (!byName.has(name)) byName.set(name, { name, locked: false });
    }
    return [...byName.values()];
  }, [skills, selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        o.name.toLowerCase().includes(q) ||
        (o.description ?? "").toLowerCase().includes(q) ||
        (o.category ?? "").toLowerCase().includes(q),
    );
  }, [options, query]);

  const toggle = (name: string, on: boolean): void => {
    const next = on
      ? [...selected, name]
      : selected.filter((s) => s !== name);
    onChange(setSkills(formData, next));
  };

  return (
    <section data-testid="af-skills" style={SECTION}>
      <Heading>
        {t("agent_form.section_skills")}
        <FieldHelp
          text={t("agent_form.section_skills_help")}
          testId="af-skills"
        />
      </Heading>
      <Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
        {t("agent_form.skills_hint")}
      </Text>

      {options.length > 6 && (
        <Input
          allowClear
          style={{ marginBottom: 12 }}
          value={query}
          data-testid="af-skills-search"
          aria-label={t("agent_form.skills_search")}
          placeholder={t("agent_form.skills_search")}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}

      <div data-testid="af-skills-list">
        {filtered.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t("agent_form.skills_empty")}
          />
        ) : (
          filtered.map((o) => {
            const checked = selected.includes(o.name);
            const row = (
              <div
                key={o.name}
                data-testid={`af-skill-row-${o.name}`}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  padding: "8px 0",
                  borderBottom: "1px solid var(--hx-border, rgba(255,255,255,0.06))",
                  opacity: o.locked ? 0.55 : 1,
                }}
              >
                <Checkbox
                  checked={checked}
                  disabled={o.locked && !checked}
                  data-testid={`af-skill-check-${o.name}`}
                  aria-label={o.name}
                  onChange={(e) => toggle(o.name, e.target.checked)}
                  style={{ marginTop: 2 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <Text strong>{o.name}</Text>
                    <Tag
                      color={o.source === "platform" ? "purple" : "default"}
                      style={{ margin: 0 }}
                    >
                      {o.source === "platform"
                        ? t("agent_form.skills_source_platform")
                        : t("agent_form.skills_source_tenant")}
                    </Tag>
                    {o.category && (
                      <Tag color="cyan" style={{ margin: 0 }}>
                        {o.category}
                      </Tag>
                    )}
                    {o.locked && (
                      <Tag color="gold" style={{ margin: 0 }}>
                        {t("agent_form.skills_tier_locked", {
                          tier: o.requiredTier ?? "",
                        })}
                      </Tag>
                    )}
                  </div>
                  {o.description && (
                    <Text
                      type="secondary"
                      style={{ display: "block", fontSize: 13 }}
                    >
                      {o.description}
                    </Text>
                  )}
                </div>
              </div>
            );
            return o.locked ? (
              <Tooltip
                key={o.name}
                title={t("agent_form.skills_tier_locked_hint", {
                  tier: o.requiredTier ?? "",
                })}
              >
                {row}
              </Tooltip>
            ) : (
              row
            );
          })
        )}
      </div>
    </section>
  );
}
