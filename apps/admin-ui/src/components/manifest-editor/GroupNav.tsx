/**
 * Left-hand group-nav tree for the manifest editor's group-nav + detail-pane
 * layout. Presentation only: all node data comes from ``CONFIG_GROUPS``; the
 * caller owns which group/section is active and what happens on selection.
 */
import { useTranslation } from "react-i18next";
import { Menu, type MenuProps } from "antd";

import { CONFIG_GROUPS } from "./groups";

/** A caller-supplied node rendered ABOVE the registered groups — e.g. an
 * Agent template's marketplace-metadata section. Mirrors ManifestEditor's
 * ``LeadingTab`` but for the nav tree. */
export interface GroupNavLeading {
  value: string;
  label: string;
}

interface GroupNavProps {
  active: string;
  onSelect: (id: string) => void;
  leading?: GroupNavLeading;
}

export function GroupNav({ active, onSelect, leading }: GroupNavProps) {
  const { t } = useTranslation();

  const groupItems: MenuProps["items"] = CONFIG_GROUPS.map((group) => ({
    key: group.id,
    label: t(group.labelKey),
    "data-testid": `cfg-nav-${group.id}`,
  }));

  const items: MenuProps["items"] = leading
    ? [
        {
          key: leading.value,
          label: leading.label,
          "data-testid": `cfg-nav-${leading.value}`,
        },
        ...(groupItems ?? []),
      ]
    : groupItems;

  return (
    <Menu
      mode="inline"
      selectedKeys={[active]}
      onClick={({ key }) => onSelect(key)}
      items={items}
    />
  );
}
