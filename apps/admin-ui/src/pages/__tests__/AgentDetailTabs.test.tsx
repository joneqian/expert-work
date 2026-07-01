/**
 * AgentDetail per-agent tabs — Stream H.6 PR 2.
 *
 * The four list SDKs are stubbed; each test asserts the tab renders
 * its rows AND passes the agent-scoped filter params (the contract
 * with H.6 PR 1's backend filters).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import * as conversationsSdk from "../../api/conversations";
import * as skillsSdk from "../../api/skills";
import * as triggersSdk from "../../api/triggers";
import * as memorySdk from "../../api/memory";
import type { AgentDetailResponse } from "../../api/agents";
import { ConversationsTab } from "../agent_detail/ConversationsTab";
import { MemoryTab } from "../agent_detail/MemoryTab";
import { SkillsTab } from "../agent_detail/SkillsTab";
import { TriggersTab } from "../agent_detail/TriggersTab";

const detail: AgentDetailResponse = {
  record: {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "22222222-2222-2222-2222-222222222222",
    name: "code-reviewer",
    version: "1.0.0",
    status: "active",
    spec_sha256: "a".repeat(64),
    created_by: "user-1",
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:00:00Z",
    spec: {},
  },
} as AgentDetailResponse;

function inRouter(node: React.ReactElement) {
  return render(<MemoryRouter>{node}</MemoryRouter>);
}

afterEach(() => vi.restoreAllMocks());

describe("ConversationsTab", () => {
  it("lists the agent's conversations with rollup and passes name+version filters", async () => {
    const spy = vi.spyOn(conversationsSdk, "listConversations").mockResolvedValue({
      items: [
        {
          thread_id: "44444444-4444-4444-4444-444444444444",
          tenant_id: detail.record.tenant_id,
          user_id: "88888888-8888-8888-8888-888888888888",
          agent_name: "code-reviewer",
          agent_version: "1.0.0",
          title: "refund question",
          status: "active",
          created_at: "2026-06-12T01:00:00Z",
          updated_at: "2026-06-12T01:05:00Z",
          run_count: 3,
          error_count: 1,
          pending_count: 0,
          last_run_at: "2026-06-12T01:05:00Z",
          tokens: {
            input_tokens: 100,
            output_tokens: 20,
            cache_creation_tokens: 0,
            cache_read_tokens: 0,
            total_tokens: 120,
            llm_calls: 2,
            models: ["claude-sonnet-4-5"],
          },
        },
      ],
      total: 1,
      cross_tenant: false,
    });

    inRouter(<ConversationsTab detail={detail} />);

    await waitFor(() => expect(screen.getByText("refund question")).toBeInTheDocument());
    // Error rollup surfaces a per-conversation error indicator.
    expect(
      screen.getByTestId("conversation-error-44444444-4444-4444-4444-444444444444"),
    ).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ agentName: "code-reviewer", agentVersion: "1.0.0" }),
    );
  });

  it("shows the empty state when the agent has no conversations", async () => {
    vi.spyOn(conversationsSdk, "listConversations").mockResolvedValue({
      items: [],
      total: 0,
      cross_tenant: false,
    });

    inRouter(<ConversationsTab detail={detail} />);

    await waitFor(() =>
      expect(screen.getByText("No conversations for this agent yet.")).toBeInTheDocument(),
    );
  });
});

describe("SkillsTab", () => {
  it("lists agent-authored skills and passes createdByAgentName", async () => {
    const spy = vi.spyOn(skillsSdk, "listSkills").mockResolvedValue({
      items: [
        {
          id: "55555555-5555-5555-5555-555555555555",
          tenant_id: detail.record.tenant_id,
          name: "summarise-prs",
          status: "active",
          latest_version: 3,
          description: "",
          category: "data",
          visibility: "tenant",
          pinned: false,
          last_used_at: null,
          state_changed_at: "2026-06-12T00:00:00Z",
          created_at: "2026-06-12T00:00:00Z",
          updated_at: "2026-06-12T00:00:00Z",
        } as skillsSdk.SkillRecord,
      ],
      platform_items: [],
      next_cursor: null,
      cross_tenant: false,
    });

    inRouter(<SkillsTab detail={detail} />);

    await waitFor(() => expect(screen.getByText("summarise-prs")).toBeInTheDocument());
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ createdByAgentName: "code-reviewer" }),
    );
  });

  it("shows the authored-empty state", async () => {
    vi.spyOn(skillsSdk, "listSkills").mockResolvedValue({
      items: [],
      platform_items: [],
      next_cursor: null,
      cross_tenant: false,
    });

    inRouter(<SkillsTab detail={detail} />);

    await waitFor(() =>
      expect(
        screen.getByText("This agent has not authored any skills yet."),
      ).toBeInTheDocument(),
    );
  });
});

describe("TriggersTab", () => {
  it("lists version-bound triggers and passes name+version filters", async () => {
    const spy = vi.spyOn(triggersSdk, "listTriggers").mockResolvedValue({
      items: [
        {
          id: "66666666-6666-6666-6666-666666666666",
          tenant_id: detail.record.tenant_id,
          user_id: null,
          agent_name: "code-reviewer",
          agent_version: "1.0.0",
          name: "nightly-review",
          kind: "cron",
          config: { expr: "0 9 * * *" },
          enabled: true,
          source: "api",
          created_at: "2026-06-12T00:00:00Z",
          updated_at: "2026-06-12T00:00:00Z",
        },
      ],
      total: 1,
      cross_tenant: false,
    });

    inRouter(<TriggersTab detail={detail} />);

    await waitFor(() => expect(screen.getByText("nightly-review")).toBeInTheDocument());
    expect(screen.getByText("enabled")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ agentName: "code-reviewer", agentVersion: "1.0.0" }),
    );
  });
});

describe("MemoryTab", () => {
  it("lists per-user memory items and states the user-scope semantics", async () => {
    const spy = vi.spyOn(memorySdk, "listMemories").mockResolvedValue({
      items: [
        {
          id: "77777777-7777-7777-7777-777777777777",
          tenant_id: detail.record.tenant_id,
          user_id: "88888888-8888-8888-8888-888888888888",
          kind: "fact",
          content: "prefers terse answers",
          created_at: "2026-06-12T00:00:00Z",
          importance: 0.5,
          confidence: 0.5,
        },
      ],
      total: 1,
      cross_tenant: false,
    });

    inRouter(<MemoryTab />);

    await waitFor(() =>
      expect(screen.getByText("prefers terse answers")).toBeInTheDocument(),
    );
    // Mini-ADR H-13 — the per-user scope is stated, not hidden.
    expect(screen.getByTestId("memory-tab-scope-note")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ kind: undefined }));
  });
});
