/**
 * YAML serialise/parse for the manifest editor. Single js-yaml instance so
 * the Form and YAML views can't disagree on formatting. ``dumpYaml`` mirrors
 * the options used elsewhere in the UI (``lineWidth: 120``).
 */
import { dump, load } from "js-yaml";

export function parseYaml(text: string): unknown {
  return load(text);
}

export function dumpYaml(value: unknown): string {
  return dump(value, { lineWidth: 120, noRefs: true });
}
