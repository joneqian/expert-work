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

  // Build a fully nested folder tree from the slash-delimited paths. Deep
  // skills (e.g. anthropics/skills pptx ships
  // ``scripts/office/schemas/ecma/fouth-edition/*.xsd``) must render as real
  // nested subfolders, not flat full-path leaves — the old single-level
  // grouping dumped everything below the first segment as one flat list.
  interface MutDir {
    dirs: Map<string, MutDir>;
    files: string[];
  }
  const mutRoot: MutDir = { dirs: new Map(), files: [] };
  for (const p of paths) {
    const segs = p.split("/");
    let node = mutRoot;
    for (let i = 0; i < segs.length - 1; i++) {
      const seg = segs[i];
      let child = node.dirs.get(seg);
      if (child === undefined) {
        child = { dirs: new Map(), files: [] };
        node.dirs.set(seg, child);
      }
      node = child;
    }
    node.files.push(p);
  }

  // Render recursively: folders first (alphabetical), then files
  // (alphabetical). Folder keys carry the full path (``dir:scripts/office``)
  // so expand state + selection never collide across same-named subfolders.
  const toNodes = (node: MutDir, prefix: string): DataNode[] => {
    const out: DataNode[] = [];
    const sortedDirs = Array.from(node.dirs.entries()).sort(([a], [b]) =>
      a.localeCompare(b),
    );
    for (const [seg, child] of sortedDirs) {
      const childPrefix = prefix ? `${prefix}/${seg}` : seg;
      out.push({
        key: `dir:${childPrefix}`,
        title: (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Folder size={13} strokeWidth={1.5} />
            <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>{seg}/</Text>
          </span>
        ),
        selectable: false,
        children: toNodes(child, childPrefix),
      });
    }
    for (const path of node.files.slice().sort()) {
      const name = path.slice(path.lastIndexOf("/") + 1);
      out.push({
        key: path,
        title: (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <FileCode2 size={12} strokeWidth={1.5} />
            <Text style={{ fontFamily: "var(--hx-font-mono)", fontSize: 12 }}>{name}</Text>
          </span>
        ),
        isLeaf: true,
      });
    }
    return out;
  };

  for (const node of toNodes(mutRoot, "")) {
    root.push(node);
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
    // Expand every ancestor directory so the full nested tree is visible.
    const dirs = new Set<string>();
    for (const p of paths) {
      const segs = p.split("/");
      let prefix = "";
      for (let i = 0; i < segs.length - 1; i++) {
        prefix = prefix ? `${prefix}/${segs[i]}` : segs[i];
        dirs.add(`dir:${prefix}`);
      }
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
