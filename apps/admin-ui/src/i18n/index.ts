/**
 * i18n bootstrap — Stream H.1b PR 2a.
 *
 * Two locales: ``zh-CN`` (default for unrecognized languages) and
 * ``en``. The detector reads from ``localStorage["expert_work.admin.lang"]``
 * first so explicit user choice survives reloads; otherwise it falls
 * back to ``navigator.language``. Both locale modules export the same
 * key tree, so adding a key in one but not the other is a typecheck
 * error (see ``locales/zh-CN.ts``'s ``TranslationKeys`` import).
 */
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import en from "./locales/en";
import zhCN from "./locales/zh-CN";

export const SUPPORTED_LANGS = ["zh-CN", "en"] as const;
export type SupportedLang = (typeof SUPPORTED_LANGS)[number];
export const LANG_STORAGE_KEY = "expert_work.admin.lang";

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      "zh-CN": { translation: zhCN },
      en: { translation: en },
    },
    fallbackLng: "zh-CN",
    supportedLngs: [...SUPPORTED_LANGS],
    interpolation: {
      // React already escapes on render.
      escapeValue: false,
    },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANG_STORAGE_KEY,
      caches: ["localStorage"],
    },
  });

export default i18n;
