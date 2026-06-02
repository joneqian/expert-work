/**
 * Process-lifetime cache + pure lookups over the model catalog. Mirrors
 * ``schema.ts``. Lookups are plain functions so they're trivially testable.
 */
import {
  fetchModelCatalog,
  type CatalogModel,
  type ModelCatalog,
} from "../../api/model_catalog";

let cached: Promise<ModelCatalog> | null = null;

export function loadModelCatalog(): Promise<ModelCatalog> {
  if (cached === null) {
    cached = fetchModelCatalog();
  }
  return cached;
}

export function __resetCatalogCacheForTest(): void {
  cached = null;
}

export function providerNames(catalog: ModelCatalog): string[] {
  return catalog.providers.map((p) => p.provider);
}

export function modelsFor(catalog: ModelCatalog, provider: string): CatalogModel[] {
  return catalog.providers.find((p) => p.provider === provider)?.models ?? [];
}

export function lookupModel(
  catalog: ModelCatalog,
  provider: string,
  name: string,
): CatalogModel | undefined {
  return modelsFor(catalog, provider).find((m) => m.name === name);
}
