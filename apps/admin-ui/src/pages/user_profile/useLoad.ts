/**
 * Best-effort loader hook shared by the UserProfile panes — each tab
 * fails independently (mirrors the local hook in ``UserDetail``).
 */
import { useEffect, useState } from "react";

import { ApiError } from "../../api/client";

export function useLoad<T>(load: () => Promise<T>): {
  data: T | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    load()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [load]);
  return { data, loading, error };
}

/** ``ApiError`` → ``${code}: ${message}`` for mutation error surfaces. */
export function errMessage(err: unknown): string {
  return err instanceof ApiError
    ? `${err.code}: ${err.message}`
    : err instanceof Error
      ? err.message
      : "unknown error";
}
