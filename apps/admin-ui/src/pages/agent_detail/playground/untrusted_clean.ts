/** 剥离不可信内容标记(spotlight 防注入机制产物),供调试台可读展示。
 *  围栏 «UNTRUSTED nonce=…» / «/UNTRUSTED nonce=…»(见 common/spotlight.py)
 *  折叠为一个 hadUntrusted 标志;datamark 的 ▁(U+2581)字形剥除。
 *  raw「查看原文」层不跑此 util —— 那里要看原始标记。 */
const FENCE_OPEN = /«UNTRUSTED nonce=[^»]*»\n?/g;
const FENCE_CLOSE = /\n?«\/UNTRUSTED nonce=[^»]*»/g;
const GLYPH = /▁/g;

export function cleanUntrusted(text: string): { text: string; hadUntrusted: boolean } {
  const hadUntrusted = text.includes("«UNTRUSTED nonce=");
  const cleaned = text.replace(FENCE_OPEN, "").replace(FENCE_CLOSE, "").replace(GLYPH, "");
  return { text: cleaned, hadUntrusted };
}
