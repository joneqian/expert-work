import Markdown from "markdown-to-jsx";

/** markdown-to-jsx renders to React elements (never ``dangerouslySetInnerHTML``);
 *  ``disableParsingRawHTML`` additionally drops any inline ``<script>``/``<img>``
 *  the agent might emit, so untrusted model output can't inject markup. Links
 *  open in a new tab with ``noreferrer`` to avoid tab-nabbing. */
const MD_OPTIONS = {
  disableParsingRawHTML: true,
  overrides: {
    a: { props: { target: "_blank", rel: "noreferrer noopener" } },
  },
} as const;

/** Render assistant/message text as markdown inside the ``.ew-markdown`` scope
 *  (see ``theme/global.css``). Used for Playground live answers and resumed
 *  history so ``##``/``**``/lists/tables/code render instead of showing raw
 *  source. */
export function MarkdownView({ children }: { children: string }) {
  return (
    <div className="ew-markdown">
      <Markdown options={MD_OPTIONS}>{children}</Markdown>
    </div>
  );
}
