/**
 * Skill file tree — Capability Uplift Sprint #3 PR C, Mini-ADR U-20.
 *
 * Left pane of the SkillDetail editor view. Renders:
 *
 *   - ``SKILL.md`` always pinned at the top (the canonical view of the
 *     version's prompt fragment + helix-namespaced frontmatter).
 *   - Supporting files grouped by their top-level directory segment
 *     (``reference/``, ``scripts/``, ``templates/``, …) so the tree
 *     mirrors what an operator would see if they unzipped the export.
 *   - A "+ Add file" entry at the bottom that opens the upload modal.
 *
 * Selection is controlled by the parent (``SkillDetail``); this
 * component does not own state. Clicks on a file path call back through
 * ``onSelect``; the parent gates the click on the "do you have unsaved
 * changes?" warning before re-rendering the editor.
 */
import { useMemo } from "react";
import { Empty, Tree, Typography } from "antd";
import type { DataNode } from "antd/es/tree";
import { FileCode2, FilePlus, Folder, FolderOpen } from "lucide-react";
import { useTranslation } from "react-i18next";

const { Text } = Typography;

export const SKILL_MD_PATH = "SKILL.md";
const ADD_FILE_NODE_KEY = "__add_file__";

interface FileTreeProps {
  paths: readonly string[];
  selected: string | null;
  onSelect: (path: string) => void;
  onAddFile: () => void;
  disabled?: boolean;
}

function buildTree(paths: readonly string[], t: (k: string) => string): DataNode[] {
  // SKILL.md gets a fixed first slot regardless of supporting_files state.
  const root: DataNode[] = [
    {
      key: SKILL_MD_PATH,
      title: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <FileCode2 size={13} strokeWidth={1.5} />
          <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>
            {SKILL_MD_PATH}
          </Text>
        </span>
      ),
      isLeaf: true,
    },
  ];

  // Group by top-level segment: e.g. "reference/foo.md" → group "reference"
  // with leaf "foo.md".
  const grouped = new Map<string, string[]>();
  const topLevel: string[] = [];
  for (const p of paths) {
    const slash = p.indexOf("/");
    if (slash < 0) {
      topLevel.push(p);
    } else {
      const dir = p.slice(0, slash);
      const arr = grouped.get(dir) ?? [];
      arr.push(p);
      grouped.set(dir, arr);
    }
  }

  // Stable order: directories alphabetically, then top-level files.
  for (const dir of Array.from(grouped.keys()).sort()) {
    const children = (grouped.get(dir) ?? []).slice().sort();
    root.push({
      key: `dir:${dir}`,
      title: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Folder size={13} strokeWidth={1.5} />
          <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>{dir}/</Text>
        </span>
      ),
      selectable: false,
      children: children.map((path) => ({
        key: path,
        title: (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <FileCode2 size={12} strokeWidth={1.5} />
            <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>
              {path.slice(dir.length + 1)}
            </Text>
          </span>
        ),
        isLeaf: true,
      })),
    });
  }

  for (const path of topLevel.sort()) {
    root.push({
      key: path,
      title: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <FileCode2 size={13} strokeWidth={1.5} />
          <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>{path}</Text>
        </span>
      ),
      isLeaf: true,
    });
  }

  // Pseudo "+ Add file" leaf (sentinel key — the click handler
  // intercepts and never calls onSelect for it).
  root.push({
    key: ADD_FILE_NODE_KEY,
    title: (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          color: "var(--hx-text-tertiary)",
          fontSize: 12,
        }}
      >
        <FilePlus size={13} strokeWidth={1.5} />
        {t("skills.file_add")}
      </span>
    ),
    isLeaf: true,
  });

  return root;
}

export function FileTree({
  paths,
  selected,
  onSelect,
  onAddFile,
  disabled = false,
}: FileTreeProps) {
  const { t } = useTranslation();
  const treeData = useMemo(() => buildTree(paths, t), [paths, t]);
  const expandedKeys = useMemo(() => {
    const dirs = new Set<string>();
    for (const p of paths) {
      const slash = p.indexOf("/");
      if (slash > 0) dirs.add(`dir:${p.slice(0, slash)}`);
    }
    return Array.from(dirs);
  }, [paths]);

  if (paths.length === 0) {
    return (
      <div data-testid="skill-file-tree">
        <Tree<DataNode>
          treeData={[treeData[0], treeData[treeData.length - 1]]}
          selectedKeys={selected ? [selected] : []}
          onSelect={(keys) => {
            const key = keys[0];
            if (typeof key !== "string") return;
            if (key === ADD_FILE_NODE_KEY) {
              onAddFile();
            } else if (!disabled) {
              onSelect(key);
            }
          }}
          showIcon={false}
          switcherIcon={null}
          blockNode
          style={{ background: "transparent" }}
        />
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("skills.detail_no_supporting_files")}
            </Text>
          }
          style={{ marginTop: 12 }}
        />
      </div>
    );
  }

  return (
    <div data-testid="skill-file-tree">
      <Tree<DataNode>
        treeData={treeData}
        selectedKeys={selected ? [selected] : []}
        defaultExpandedKeys={expandedKeys}
        onSelect={(keys) => {
          const key = keys[0];
          if (typeof key !== "string") return;
          if (key === ADD_FILE_NODE_KEY) {
            onAddFile();
            return;
          }
          if (disabled) return;
          onSelect(key);
        }}
        switcherIcon={({ expanded }) =>
          expanded ? (
            <FolderOpen size={11} strokeWidth={1.5} />
          ) : (
            <Folder size={11} strokeWidth={1.5} />
          )
        }
        showIcon={false}
        blockNode
        style={{ background: "transparent" }}
      />
    </div>
  );
}
