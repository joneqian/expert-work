/**
 * Monaco-based editor for the system-prompt template when Jinja mode is on.
 * Over a plain textarea it adds: syntax highlight for ``{{ }}`` / ``{% %}`` /
 * ``{# #}`` delimiters and Jinja keywords, plus autocomplete fed by the agent's
 * declared prompt variables (type ``{{`` → pick a declared name). The Jinja
 * language + completion provider are global to Monaco, so they register once;
 * the live variable list is shared through a module ref the provider reads.
 *
 * The testid lives on the wrapper div because ``@monaco-editor/react`` does not
 * forward ``data-testid`` to a queryable node in a real browser.
 */
import { useEffect } from "react";
import Editor, { type Monaco } from "@monaco-editor/react";

/**
 * Minimal structural types for the completion callback. The ``monaco-editor``
 * module is a hoisted transitive dep (not directly importable from this app),
 * so rather than import its declarations we type only the members we touch.
 */
interface CompletionModel {
  getWordUntilPosition(position: CompletionPosition): {
    startColumn: number;
    endColumn: number;
  };
}
interface CompletionPosition {
  lineNumber: number;
}

import type { PromptVariableFields } from "../form_model";

const LANG_ID = "jinja-prompt";
const THEME_ID = "jinja-dark";

/** Latest declared variables, read by the (global) completion provider. */
let activeVariables: PromptVariableFields[] = [];
let languageRegistered = false;

function registerJinja(monaco: Monaco): void {
  if (languageRegistered) return;
  languageRegistered = true;

  monaco.languages.register({ id: LANG_ID });
  monaco.languages.setMonarchTokensProvider(LANG_ID, {
    tokenizer: {
      root: [
        [/\{\{/, { token: "delimiter.jinja", next: "@expr" }],
        [/\{%/, { token: "delimiter.jinja", next: "@stmt" }],
        [/\{#/, { token: "comment.jinja", next: "@comment" }],
        [/[^{]+/, "source"],
        [/\{/, "source"],
      ],
      expr: [
        [/\}\}/, { token: "delimiter.jinja", next: "@pop" }],
        [/\|/, "operator.jinja"],
        [/[a-zA-Z_]\w*/, "variable.jinja"],
        [/[^}]/, "string.jinja"],
      ],
      stmt: [
        [/%\}/, { token: "delimiter.jinja", next: "@pop" }],
        [
          /\b(if|elif|else|endif|for|endfor|in|set|block|endblock|macro|endmacro|not|and|or|is)\b/,
          "keyword.jinja",
        ],
        [/[a-zA-Z_]\w*/, "variable.jinja"],
        [/[^%]/, "string.jinja"],
      ],
      comment: [
        [/#\}/, { token: "comment.jinja", next: "@pop" }],
        [/./, "comment.jinja"],
      ],
    },
  });

  monaco.editor.defineTheme(THEME_ID, {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "delimiter.jinja", foreground: "c792ea", fontStyle: "bold" },
      { token: "keyword.jinja", foreground: "c792ea" },
      { token: "variable.jinja", foreground: "82aaff" },
      { token: "operator.jinja", foreground: "89ddff" },
      { token: "comment.jinja", foreground: "636d83", fontStyle: "italic" },
    ],
    colors: {},
  });

  monaco.languages.registerCompletionItemProvider(LANG_ID, {
    triggerCharacters: ["{", " "],
    provideCompletionItems(
      model: CompletionModel,
      position: CompletionPosition,
    ) {
      const word = model.getWordUntilPosition(position);
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };
      const suggestions = activeVariables
        .filter((v) => v.name)
        .map((v) => ({
          label: v.name as string,
          kind: monaco.languages.CompletionItemKind.Variable,
          insertText: v.name as string,
          detail: v.description || undefined,
          range,
        }));
      return { suggestions };
    },
  });
}

interface PromptTemplateEditorProps {
  value: string;
  variables: PromptVariableFields[];
  onChange: (value: string) => void;
}

export function PromptTemplateEditor({
  value,
  variables,
  onChange,
}: PromptTemplateEditorProps) {
  // Keep the global completion provider pointed at this agent's declared vars.
  useEffect(() => {
    activeVariables = variables;
    return () => {
      activeVariables = [];
    };
  }, [variables]);

  return (
    <div data-testid="af-prompt-monaco">
      <Editor
        language={LANG_ID}
        theme={THEME_ID}
        value={value}
        height={240}
        beforeMount={registerJinja}
        onChange={(v) => onChange(v ?? "")}
        options={{
          minimap: { enabled: false },
          fontFamily: "var(--hx-font-mono)",
          fontSize: 13,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          wordWrap: "on",
          quickSuggestions: { other: true, strings: true, comments: false },
        }}
      />
    </div>
  );
}
