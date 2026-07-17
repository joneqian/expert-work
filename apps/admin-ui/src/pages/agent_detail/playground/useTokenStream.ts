/**
 * useTokenStream — accumulates live `content` token SSE frames (流式 epic 子项目 3a)
 * into a per-step buffer for the playground's typewriter view.
 *
 * Token frames are high-frequency and deliberately kept OUT of `turn.events`
 * (so the O(n) `parseTimeline`/`summarizeTurn` memos stay stable during token
 * flow). This hook holds the buffer in a mutable ref and flushes to React state
 * once per animation frame — many tokens in one frame cause a single re-render.
 * The authoritative `updates` frame remains the source of truth; a step's live
 * buffer is superseded at render time once its authoritative card exists.
 */
import { useCallback, useRef, useState } from "react";

import type { SseEvent } from "../../../api/sessions";

export interface TokenStreamState {
  /** step index → accumulated (already server-redacted) content text. */
  liveByStep: ReadonlyMap<number, string>;
  /** ms from run start to the first token; null until the first token. */
  ttftMs: number | null;
  /** true once the run ended; live steps without an authoritative card are interrupted. */
  finalized: boolean;
}

export interface TokenStreamController extends TokenStreamState {
  /** Feed one SSE frame; only `token`/`channel:"content"` frames mutate state. */
  push: (frame: SseEvent) => void;
  /** Begin a new run: clear buffers + finalized flag, record the start time. */
  reset: () => void;
  /** End the run: final flush, mark finalized (keeps buffered partial text). */
  finalize: () => void;
}

interface TokenFrameData {
  step: number;
  text: string;
}

function parseContentToken(frame: SseEvent): TokenFrameData | null {
  if (frame.event !== "token") return null;
  const d = frame.data;
  if (d === null || typeof d !== "object") return null;
  const rec = d as Record<string, unknown>;
  if (typeof rec.step !== "number" || rec.channel !== "content" || typeof rec.text !== "string") {
    return null;
  }
  return { step: rec.step, text: rec.text };
}

const EMPTY: ReadonlyMap<number, string> = new Map();

export function useTokenStream(): TokenStreamController {
  const bufRef = useRef<Map<number, string>>(new Map());
  const startRef = useRef<number | null>(null);
  const ttftRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const [snapshot, setSnapshot] = useState<TokenStreamState>({
    liveByStep: EMPTY,
    ttftMs: null,
    finalized: false,
  });

  const flush = useCallback(() => {
    rafRef.current = null;
    setSnapshot((prev) => ({
      liveByStep: new Map(bufRef.current),
      ttftMs: ttftRef.current,
      finalized: prev.finalized,
    }));
  }, []);

  const schedule = useCallback(() => {
    if (rafRef.current === null) rafRef.current = requestAnimationFrame(flush);
  }, [flush]);

  const cancel = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  }, []);

  const push = useCallback(
    (frame: SseEvent) => {
      const tok = parseContentToken(frame);
      if (tok === null) return;
      if (ttftRef.current === null && startRef.current !== null) {
        ttftRef.current = Date.now() - startRef.current;
      }
      bufRef.current.set(tok.step, (bufRef.current.get(tok.step) ?? "") + tok.text);
      schedule();
    },
    [schedule],
  );

  const reset = useCallback(() => {
    cancel();
    bufRef.current = new Map();
    startRef.current = Date.now();
    ttftRef.current = null;
    setSnapshot({ liveByStep: EMPTY, ttftMs: null, finalized: false });
  }, [cancel]);

  const finalize = useCallback(() => {
    cancel();
    setSnapshot({ liveByStep: new Map(bufRef.current), ttftMs: ttftRef.current, finalized: true });
  }, [cancel]);

  return { ...snapshot, push, reset, finalize };
}
