/**
 * Tool-call timeline parser.
 *
 * Walks a run's SSE ``updates`` frames (LangGraph ``{node: {messages}}``
 * chunks, each message a ``BaseMessage.model_dump()``) and reconstructs the
 * agent's tool activity: every ``AIMessage.tool_calls[]`` is a CALL, every
 * ``ToolMessage`` (linked by ``tool_call_id``) is its RESULT. The result
 * message carries no tool name (LangChain quirk), so the name + args come
 * from the call side.
 *
 * MCP tools are registered as ``mcp:{server}.{tool}`` (orchestrator
 * ``MCPTool``), so we attribute the originating MCP server from the name.
 * Builtin tools (``web_search``, ``exec_python``, …) keep their bare name.
 */
import type { SseEvent } from "./sessions";

export type ToolCallStatus = "pending" | "success" | "error";

export interface ToolCallEntry {
  /** ``tool_call_id`` — links the call to its result. */
  id: string;
  /** Raw tool name as the LLM called it (e.g. ``mcp:amap-maps.maps_direction_driving``). */
  rawName: string;
  isMcp: boolean;
  /** MCP server name when ``isMcp`` (else ``null``). */
  server: string | null;
  /** Display tool name — the ``mcp:server.`` prefix stripped. */
  toolName: string;
  args: Record<string, unknown>;
  status: ToolCallStatus;
  /** Result text with the spotlight ``«UNTRUSTED…»`` fence stripped (``null`` until the result arrives). */
  resultPreview: string | null;
}

const MCP_PREFIX = "mcp:";
// Spotlight injection-defense fence lines wrapping untrusted tool output.
const SPOTLIGHT_FENCE = /«\/?UNTRUSTED[^»]*»/g;

interface ParsedName {
  isMcp: boolean;
  server: string | null;
  toolName: string;
}

function parseName(raw: string): ParsedName {
  if (raw.startsWith(MCP_PREFIX)) {
    const rest = raw.slice(MCP_PREFIX.length); // "server.tool"
    const dot = rest.indexOf(".");
    if (dot > 0) {
      return { isMcp: true, server: rest.slice(0, dot), toolName: rest.slice(dot + 1) };
    }
    return { isMcp: true, server: null, toolName: rest };
  }
  return { isMcp: false, server: null, toolName: raw };
}

function stripFence(content: string): string {
  return content.replace(SPOTLIGHT_FENCE, "").trim();
}

/** Flatten the messages across every node in one ``updates`` chunk. */
export function messagesOf(data: unknown): Array<Record<string, unknown>> {
  if (data === null || typeof data !== "object") return [];
  const out: Array<Record<string, unknown>> = [];
  for (const nodeVal of Object.values(data as Record<string, unknown>)) {
    if (nodeVal !== null && typeof nodeVal === "object") {
      const msgs = (nodeVal as Record<string, unknown>).messages;
      if (Array.isArray(msgs)) {
        for (const m of msgs) {
          if (m !== null && typeof m === "object") out.push(m as Record<string, unknown>);
        }
      }
    }
  }
  return out;
}

/** Reconstruct the ordered tool-call timeline from a run's SSE frames. */
export function parseToolCalls(events: readonly SseEvent[]): ToolCallEntry[] {
  const order: string[] = [];
  const byId = new Map<string, ToolCallEntry>();

  const ensure = (id: string, init: () => ToolCallEntry): ToolCallEntry => {
    let entry = byId.get(id);
    if (entry === undefined) {
      entry = init();
      byId.set(id, entry);
      order.push(id);
    }
    return entry;
  };

  for (const evt of events) {
    if (evt.event !== "updates") continue;
    for (const m of messagesOf(evt.data)) {
      // Call side — an AIMessage carrying tool_calls.
      if (m.type === "ai" && Array.isArray(m.tool_calls)) {
        for (const tc of m.tool_calls as Array<Record<string, unknown>>) {
          if (typeof tc.id !== "string" || tc.id === "") continue;
          const rawName = typeof tc.name === "string" ? tc.name : "";
          const parsed = parseName(rawName);
          const args =
            tc.args !== null && typeof tc.args === "object"
              ? (tc.args as Record<string, unknown>)
              : {};
          const entry = ensure(tc.id, () => ({
            id: tc.id as string,
            rawName,
            isMcp: parsed.isMcp,
            server: parsed.server,
            toolName: parsed.toolName,
            args,
            status: "pending",
            resultPreview: null,
          }));
          // A re-seen call (replayed frame) refreshes name/args, never status.
          entry.rawName = rawName;
          entry.isMcp = parsed.isMcp;
          entry.server = parsed.server;
          entry.toolName = parsed.toolName;
          entry.args = args;
        }
      }
      // Result side — a ToolMessage linked by tool_call_id. The orchestrator
      // now stamps ``name`` on the result too; use it as a fallback when the
      // call frame was missed (truncated stream), and to seed the entry.
      if (m.type === "tool" && typeof m.tool_call_id === "string" && m.tool_call_id !== "") {
        const status: ToolCallStatus = m.status === "error" ? "error" : "success";
        const preview = typeof m.content === "string" ? stripFence(m.content) : "";
        const resultName = typeof m.name === "string" ? m.name : "";
        const entry = ensure(m.tool_call_id, () => {
          const parsed = parseName(resultName);
          return {
            id: m.tool_call_id as string,
            rawName: resultName,
            isMcp: parsed.isMcp,
            server: parsed.server,
            toolName: resultName === "" ? (m.tool_call_id as string) : parsed.toolName,
            args: {},
            status,
            resultPreview: preview,
          };
        });
        // Fill the name from the result only if the call side didn't provide it.
        if (entry.rawName === "" && resultName !== "") {
          const parsed = parseName(resultName);
          entry.rawName = resultName;
          entry.isMcp = parsed.isMcp;
          entry.server = parsed.server;
          entry.toolName = parsed.toolName;
        }
        entry.status = status;
        entry.resultPreview = preview;
      }
    }
  }

  return order.map((id) => byId.get(id) as ToolCallEntry);
}
