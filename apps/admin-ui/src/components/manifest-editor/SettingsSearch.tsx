/**
 * Group-level search box for the manifest editor's top bar (agent-config-page
 * redesign PR1) — an antd ``AutoComplete`` over ``CONFIG_GROUPS``, matched
 * via the pure ``searchGroups``.
 *
 * Selecting a result only reports the picked group id via ``onPick`` — it
 * never navigates directly. The caller (``ManifestEditor``) decides how to
 * switch groups, so the same YAML-validity guard that gates a tree-node
 * click also gates a search selection.
 */
import { useMemo, useState } from "react";
import { AutoComplete } from "antd";
import { useTranslation } from "react-i18next";

import { searchGroups } from "./groups";

interface SettingsSearchProps {
  /** Called with the picked group's id when a result is selected. */
  onPick: (groupId: string) => void;
  /** Group ids to omit from results even if they'd otherwise match — e.g.
   * groups hidden because all their sections were merged into a leading
   * tab (mirrors ``GroupNav``'s ``hiddenGroups``). */
  exclude?: readonly string[];
}

export function SettingsSearch({ onPick, exclude }: SettingsSearchProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const excluded = useMemo(() => new Set(exclude ?? []), [exclude]);

  const options = useMemo(
    () =>
      searchGroups(query, t)
        .filter((group) => !excluded.has(group.id))
        .map((group) => ({ value: group.id, label: t(group.labelKey) })),
    [query, t, excluded],
  );

  function handleSelect(groupId: string): void {
    onPick(groupId);
    setQuery("");
  }

  return (
    <AutoComplete
      value={query}
      onChange={setQuery}
      onSelect={handleSelect}
      options={options}
      placeholder={t("manifest_editor.search_placeholder")}
      aria-label={t("manifest_editor.search_placeholder")}
      style={{ width: 220 }}
      data-testid="cfg-search"
    />
  );
}
