/**
 * Image uploads SDK — Stream P (PR M, Mini-ADR P-16).
 *
 * ``POST /v1/sessions/{thread_id}/uploads`` takes a multipart ``file``
 * field and returns the ``helix://image/...`` reference the run request's
 * ``image_refs`` field carries — the Playground attaches that ref to the
 * next turn so the multimodal input path resolves bytes at LLM-call time.
 *
 * Contract note — unlike the JSON endpoints this one returns a *raw*
 * ``{ "image_ref": "..." }`` body (201), NOT the ``{ success, data,
 * error }`` envelope. So we read ``response.data.image_ref`` directly
 * rather than running it through :func:`unwrap`.
 */
import { apiClient, ApiError } from "./client";

interface UploadResponse {
  image_ref: string;
}

/** Upload one image to a thread; returns its ``helix://image/...`` ref. */
export async function uploadImage(threadId: string, file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiClient.post<UploadResponse>(
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
