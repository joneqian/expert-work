import { describe, expect, it } from "vitest";

import { artifactsFromTools, parseToolCalls } from "../tool_timeline";
import type { SseEvent } from "../sessions";

function evt(event: string, data: unknown): SseEvent {
  return { id: null, event, data, rawData: "", receivedAt: "" };
}

/** An ``updates`` frame for one node carrying message dicts. */
function updates(node: string, messages: unknown[]): SseEvent {
  return evt("updates", { [node]: { messages } });
}

function aiCall(id: string, name: string, args: Record<string, unknown>): unknown {
  return { type: "ai", content: "", tool_calls: [{ id, name, args, type: "tool_call" }] };
}

function toolResult(id: string, content: string, status = "success"): unknown {
  return { type: "tool", tool_call_id: id, name: null, content, status };
}

describe("parseToolCalls", () => {
  it("links a call to its result and parses an MCP server from the name", () => {
    const events = [
      updates("agent", [aiCall("c1", "mcp:amap-maps.maps_direction_driving", { origin: "a" })]),
      updates("tools", [
        toolResult("c1", "«UNTRUSTED nonce=x»\n{\"distance\":\"1001\"}\n«/UNTRUSTED nonce=x»"),
      ]),
    ];
    const [entry, ...rest] = parseToolCalls(events);
    expect(rest).toHaveLength(0);
    expect(entry.isMcp).toBe(true);
    expect(entry.server).toBe("amap-maps");
    expect(entry.toolName).toBe("maps_direction_driving");
    expect(entry.args).toEqual({ origin: "a" });
    expect(entry.status).toBe("success");
    // Spotlight fence stripped from the preview.
    expect(entry.resultPreview).toBe('{"distance":"1001"}');
  });

  it("treats a non-mcp name as a builtin tool", () => {
    const [entry] = parseToolCalls([updates("agent", [aiCall("c1", "web_search", { q: "hi" })])]);
    expect(entry.isMcp).toBe(false);
    expect(entry.server).toBeNull();
    expect(entry.toolName).toBe("web_search");
    expect(entry.status).toBe("pending"); // no result yet
  });

  it("marks a failed tool result as error", () => {
    const events = [
      updates("agent", [aiCall("c1", "exec_python", {})]),
      updates("tools", [toolResult("c1", "boom", "error")]),
    ];
    expect(parseToolCalls(events)[0].status).toBe("error");
  });

  it("renders a gate-blocked pending tool as pending_approval", () => {
    // The gate dispatches nothing — bash has a call but never a result.
    const events = [updates("agent", [aiCall("c1", "bash", { command: "pip install x" })])];
    // Live (not yet paused): the call reads as in-progress.
    expect(parseToolCalls(events)[0].status).toBe("pending");
    // Paused at the gate: the blocked call is awaiting approval, not stuck.
    expect(parseToolCalls(events, true)[0].status).toBe("pending_approval");
  });

  it("does not downgrade a resolved tool to pending_approval", () => {
    const events = [
      updates("agent", [aiCall("c1", "web_search", {})]),
      updates("tools", [toolResult("c1", "ok")]),
    ];
    // A completed call stays success even if a later call in the turn gated.
    expect(parseToolCalls(events, true)[0].status).toBe("success");
  });

  it("preserves call order across frames and handles multiple calls", () => {
    const events = [
      updates("agent", [aiCall("c1", "web_search", {})]),
      updates("agent", [aiCall("c2", "mcp:amap-maps.geocode", {})]),
      updates("tools", [toolResult("c2", "ok"), toolResult("c1", "ok")]),
    ];
    const out = parseToolCalls(events);
    expect(out.map((e) => e.id)).toEqual(["c1", "c2"]);
    expect(out.every((e) => e.status === "success")).toBe(true);
  });

  it("ignores non-updates frames (metadata/end)", () => {
    const events = [evt("metadata", { run_id: "r" }), evt("end", "done")];
    expect(parseToolCalls(events)).toEqual([]);
  });

  it("tolerates a result without a captured call (truncated stream)", () => {
    const out = parseToolCalls([updates("tools", [toolResult("orphan", "late")])]);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("orphan");
    expect(out[0].status).toBe("success");
  });

  it("uses the result-side name when the call frame was missed", () => {
    // Orchestrator now stamps name on the ToolMessage too.
    const named = {
      type: "tool",
      tool_call_id: "orphan",
      name: "mcp:amap-maps.geo",
      content: "{}",
      status: "success",
    };
    const [entry] = parseToolCalls([updates("tools", [named])]);
    expect(entry.isMcp).toBe(true);
    expect(entry.server).toBe("amap-maps");
    expect(entry.toolName).toBe("geo");
  });
});

describe("artifactsFromTools", () => {
  it("returns a successfully saved artifact with its name + kind", () => {
    const events = [
      updates("agent", [aiCall("c1", "save_artifact", { name: "report.pdf", kind: "document" })]),
      updates("tools", [toolResult("c1", "Saved artifact 'report.pdf' …")]),
    ];
    expect(artifactsFromTools(events)).toEqual([{ name: "report.pdf", kind: "document" }]);
  });

  it("defaults kind to 'other' when the call omitted it", () => {
    const events = [
      updates("agent", [aiCall("c1", "save_artifact", { name: "out.bin" })]),
      updates("tools", [toolResult("c1", "Saved …")]),
    ];
    expect(artifactsFromTools(events)).toEqual([{ name: "out.bin", kind: "other" }]);
  });

  it("ignores a save still pending (no result yet)", () => {
    const events = [updates("agent", [aiCall("c1", "save_artifact", { name: "report.pdf" })])];
    expect(artifactsFromTools(events)).toEqual([]);
  });

  it("ignores a failed save", () => {
    const events = [
      updates("agent", [aiCall("c1", "save_artifact", { name: "report.pdf" })]),
      updates("tools", [toolResult("c1", "disk full", "error")]),
    ];
    expect(artifactsFromTools(events)).toEqual([]);
  });

  it("dedupes a re-saved name to one chip", () => {
    const events = [
      updates("agent", [aiCall("c1", "save_artifact", { name: "report.pdf", kind: "document" })]),
      updates("tools", [toolResult("c1", "v1")]),
      updates("agent", [aiCall("c2", "save_artifact", { name: "report.pdf", kind: "document" })]),
      updates("tools", [toolResult("c2", "v2")]),
    ];
    expect(artifactsFromTools(events)).toEqual([{ name: "report.pdf", kind: "document" }]);
  });

  it("ignores non-save_artifact tools", () => {
    const events = [
      updates("agent", [aiCall("c1", "web_search", { q: "hi" })]),
      updates("tools", [toolResult("c1", "results")]),
    ];
    expect(artifactsFromTools(events)).toEqual([]);
  });
});
