/**
 * Platform Embedding/Rerank config SDK — backed by /v1/platform/embedding-config
 * (Stream T PR C). system_admin-only, platform-level.
 */
import { getJson, putJson } from "./client";

export interface ProviderModel {
  provider: string;
  model: string;
}

export interface PlatformEmbeddingConfigView {
  embedding: ProviderModel | null;
  rerank: ProviderModel | null;
  available_embedding: ProviderModel[];
  available_rerank: ProviderModel[];
}

export interface PlatformEmbeddingConfigWrite {
  embedding_provider: string;
  embedding_model: string;
  rerank_provider?: string;
  rerank_model?: string;
}

export async function getPlatformEmbeddingConfig(): Promise<PlatformEmbeddingConfigView> {
  return getJson<PlatformEmbeddingConfigView>("/v1/platform/embedding-config");
}

export async function putPlatformEmbeddingConfig(
  body: PlatformEmbeddingConfigWrite,
): Promise<{ embedding: ProviderModel | null; rerank: ProviderModel | null }> {
  return putJson("/v1/platform/embedding-config", body);
}
