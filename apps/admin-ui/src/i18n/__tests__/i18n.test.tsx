/**
 * i18n bootstrap tests — Stream H.1b PR 2a.
 *
 * Two invariants we care about:
 *
 *   1. The locale modules are structurally identical (catches missing
 *      translations early). ``TranslationKeys`` already enforces it at
 *      compile time, but a runtime check guards against accidental
 *      drift via ``as unknown`` casts in future PRs.
 *   2. ``i18next.changeLanguage`` flips ``t()`` in place — i.e. the
 *      React glue is wired.
 */
import { describe, expect, it } from "vitest";

import "../index";
import i18n from "../index";
import en from "../locales/en";
import zhCN from "../locales/zh-CN";

function collectKeys(obj: object, prefix = ""): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object") {
      out.push(...collectKeys(value, path));
    } else {
      out.push(path);
    }
  }
  return out.sort();
}

// Task 8 (config-page-redesign) — flat map of every "*_brief" leaf, keyed by
// dotted path, so the length guard below can name the offender directly
// instead of just failing on a boolean.
function collectBriefEntries(obj: object, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === "object") {
      Object.assign(out, collectBriefEntries(value, path));
    } else if (key.endsWith("_brief") && typeof value === "string") {
      out[path] = value;
    }
  }
  return out;
}

describe("locale modules", () => {
  it("zh-CN has the same key set as en", () => {
    expect(collectKeys(zhCN)).toEqual(collectKeys(en));
  });

  it("canonical MCP terminology is applied (zh)", () => {
    expect(zhCN.nav.mcp_oauth).toBe("我的 MCP 授权");
    expect(zhCN.mcp_servers.add).toBe("添加 MCP 服务器");
    expect(zhCN.mcp_servers.status_enabled).toBe("运行中");
    expect(zhCN.mcp_catalog.col_enabled).toBe("发布状态");
    expect(zhCN.mcp_oauth.page_title).toBe("我的 MCP 授权");
    // 新键存在
    expect(zhCN.mcp_servers.source_platform).toBe("平台");
    expect(zhCN.mcp_servers.needs_authorize).toBe("需你授权");
  });

  // Task 8 — brief copy is a one-glance summary for non-technical operators;
  // long explanations belong in the sibling "_impact" field instead. 24 is
  // the guard-rail ceiling (not the 18-char authoring target from the brief
  // rewrite) — it leaves room for short punctuation/digits without letting a
  // brief regress back into paragraph territory.
  it("every *_brief value (zh) stays within the length guard-rail", () => {
    const entries = collectBriefEntries(zhCN);
    const offenders = Object.entries(entries).filter(
      ([, value]) => value.length > 24,
    );
    expect(offenders).toEqual([]);
  });
});

describe("i18n runtime", () => {
  it("changeLanguage swaps the returned translations", async () => {
    await i18n.changeLanguage("en");
    expect(i18n.t("common.sign_in")).toBe("Sign in");
    await i18n.changeLanguage("zh-CN");
    expect(i18n.t("common.sign_in")).toBe("登录");
  });
});
