/**
 * Image uploads SDK — Stream P (PR M, Mini-ADR P-16).
 *
 * ``POST /v1/sessions/{thread_id}/uploads`` takes a multipart ``file``
 * field and returns the ``expert_work://image/...`` reference the run request's
 * ``image_refs`` field carries — the Playground attaches that ref to the
 * next turn so the multimodal input path resolves bytes at LLM-call time.
 *
 * Contract note — unlike the JSON endpoints this one returns a *raw*
 * ``{ "image_ref": "..." }`` body (201), NOT the ``{ success, data,
 * error }`` envelope. So we read ``response.data.image_ref`` directly
 * rather than running it through :func:`unwrap`.
 */
import { apiClient, ApiError } from "./client";

interface ImageUploadResponse {
  image_ref: string;
}

interface DocumentUploadResponse {
  path: string;
  kind: string;
}

/** Upload one image to a thread; returns its ``expert_work://image/...`` ref. */
export async function uploadImage(threadId: string, file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<ImageUploadResponse>(
    `/v1/sessions/${encodeURIComponent(threadId)}/uploads`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  const ref = response.data?.image_ref;
  if (typeof ref !== "string" || ref.length === 0) {
    throw new ApiError("upload response carried no image_ref", "EMPTY_UPLOAD", 0);
  }
  return ref;
}

/**
 * Upload one document (PDF / office / text) to a thread. The same endpoint
 * routes documents to the user's workspace (not the image object store) and
 * returns the workspace-relative ``path`` the agent's ``read_document`` reads.
 */
export async function uploadDocument(threadId: string, file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<DocumentUploadResponse>(
    `/v1/sessions/${encodeURIComponent(threadId)}/uploads`,
    form,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  const path = response.data?.path;
  if (typeof path !== "string" || path.length === 0) {
    throw new ApiError("upload response carried no document path", "EMPTY_UPLOAD", 0);
  }
  return path;
}
