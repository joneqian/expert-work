/**
 * Raw-YAML escape hatch for the manifest editor — a thin Monaco wrapper.
 * The testid lives on the wrapper div because ``@monaco-editor/react`` does
 * not forward ``data-testid`` to a queryable node in a real browser.
 */
import Editor from "@monaco-editor/react";

interface YamlViewProps {
  value: string;
  onChange: (value: string) => void;
}

export function YamlView({ value, onChange }: YamlViewProps) {
  return (
    <div data-testid="manifest-yaml-view">
      <Editor
        language="yaml"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        theme="vs-dark"
        height="calc(100vh - 300px)"
        options={{
          minimap: { enabled: false },
          fontFamily: "var(--hx-font-mono)",
          fontSize: 12,
          tabSize: 2,
          scrollBeyondLastLine: false,
          renderWhitespace: "boundary",
          wordWrap: "on",
        }}
      />
    </div>
  );
}
