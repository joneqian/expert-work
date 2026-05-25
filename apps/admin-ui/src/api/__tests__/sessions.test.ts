/**
 * Sessions SDK tests — Stream H.2 PR 3.
 *
 * Pure parser tests: the network layer is tested via the higher-level
 * ``PlaygroundTab`` component test, but the SSE frame parser is purely
 * functional and worth covering directly.
 */
import { describe, expect, it } from "vitest";

import { parseSseStream } from "../sessions";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect<T>(it: AsyncIterable<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const v of it) out.push(v);
  return out;
}

describe("parseSseStream", () => {
  it("parses one frame with id + event + JSON data", async () => {
    const body = streamOf([
      "id: 42\nevent: metadata\ndata: {\"run_id\":\"abc\"}\n\n",
    ]);
    const frames = await collect(parseSseStream(body));
    expect(frames).toHaveLength(1);
    expect(frames[0].id).toBe("42");
    expect(frames[0].event).toBe("metadata");
    expect(frames[0].data).toEqual({ run_id: "abc" });
  });

  it("yields multiple frames split across two chunks", async () => {
    const body = streamOf([
      "event: updates\ndata: {\"step\":1}\n",
      "\nevent: updates\ndata: {\"step\":2}\n\n",
    ]);
    const frames = await collect(parseSseStream(body));
    expect(frames).toHaveLength(2);
    expect(frames[0].data).toEqual({ step: 1 });
    expect(frames[1].data).toEqual({ step: 2 });
  });

  it("falls back to raw string when data isn't JSON", async () => {
    const body = streamOf(["event: end\ndata: bye\n\n"]);
    const frames = await collect(parseSseStream(body));
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe("end");
    expect(frames[0].data).toBe("bye");
    expect(frames[0].rawData).toBe("bye");
  });

  it("defaults event to 'message' when the frame omits it", async () => {
    const body = streamOf(["data: {\"x\":1}\n\n"]);
    const frames = await collect(parseSseStream(body));
    expect(frames[0].event).toBe("message");
  });

  it("skips comment lines starting with ':'", async () => {
    const body = streamOf([": heartbeat\n\nevent: metadata\ndata: {}\n\n"]);
    const frames = await collect(parseSseStream(body));
    expect(frames).toHaveLength(1);
    expect(frames[0].event).toBe("metadata");
  });
});
