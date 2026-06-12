/**
 * SDK for ``/v1/knowledge`` — Stream H.7 PR 1 (design § 6.9).
 *
 * Every endpoint is RAW (no envelope) → ``apiClient`` directly, never
 * ``getJson`` ([memory:envelope-vs-raw-contract-check]).
 *
 * Tenant semantics (Mini-ADR H-19): the backend router reads the JWT's
 * home tenant only — there is no ``tenant_id`` query support, so these
 * calls intentionally take NO TenantScope. Knowledge bases are
 * tenant-scoped shared assets (not per-user).
 */
import { apiClient } from "./client";

export type DocumentStatus = "pending" | "ingesting" | "ready" | "failed";

export interface KnowledgeBase {
  id: string;
  name: string;
  chunk_max_tokens: number;
  chunk_overlap_tokens: number;
  created_at: string | null;
}

export interface KnowledgeDocument {
  id: string;
  filename: string;
  status: DocumentStatus;
  /** Failure detail when ``status === "failed"``. */
  error: string | null;
  chunk_count: number;
  created_at: string | null;
  updated_at: string | null;
}

/** Upload whitelist — mirrors the backend's SUPPORTED_EXTENSIONS
 *  (``knowledge/parsing.py``); used for the Upload ``accept`` attr and
 *  a pre-flight check so unsupported files fail before the request. */
export const SUPPORTED_DOCUMENT_EXTENSIONS = [
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
  ".md",
  ".markdown",
  ".txt",
  ".html",
  ".htm",
  ".csv",
] as const;

export function isSupportedDocument(filename: string): boolean {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return false;
  const ext = filename.slice(dot).toLowerCase();
  return (SUPPORTED_DOCUMENT_EXTENSIONS as readonly string[]).includes(ext);
}

export async function listBases(): Promise<KnowledgeBase[]> {
  const response = await apiClient.get<{ bases: KnowledgeBase[] }>("/v1/knowledge/bases");
  return response.data.bases;
}

export async function createBase(params: {
  name: string;
  chunkMaxTokens?: number;
  chunkOverlapTokens?: number;
}): Promise<KnowledgeBase> {
  const response = await apiClient.post<KnowledgeBase>("/v1/knowledge/bases", {
    name: params.name,
    chunk_max_tokens: params.chunkMaxTokens,
    chunk_overlap_tokens: params.chunkOverlapTokens,
  });
  return response.data;
}

export async function deleteBase(name: string): Promise<void> {
  await apiClient.delete(`/v1/knowledge/bases/${encodeURIComponent(name)}`);
}

/** POST multipart — 202 Accepted; ingestion runs in the background and
 *  the caller polls ``listDocuments`` for the status (Mini-ADR H-18). */
export async function uploadDocument(baseName: string, file: File): Promise<KnowledgeDocument> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<KnowledgeDocument>(
    `/v1/knowledge/bases/${encodeURIComponent(baseName)}/documents`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
}

export async function listDocuments(baseName: string): Promise<KnowledgeDocument[]> {
  const response = await apiClient.get<{ documents: KnowledgeDocument[] }>(
    `/v1/knowledge/bases/${encodeURIComponent(baseName)}/documents`,
  );
  return response.data.documents;
}

export async function deleteDocument(baseName: string, documentId: string): Promise<void> {
  await apiClient.delete(
    `/v1/knowledge/bases/${encodeURIComponent(baseName)}/documents/${encodeURIComponent(documentId)}`,
  );
}
