import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../../i18n";

import { SkillPicker } from "../SkillPicker";
import type { AgentManifest } from "../form_model";

function rec(over: Record<string, unknown>) {
  return {
    id: over.name,
    status: "active",
    latest_version: 1,
    description: "",
    category: "general",
    pinned: false,
    last_used_at: null,
    state_changed_at: null,
    created_at: "",
    updated_at: "",
    ...over,
  };
}

vi.mock("../../../api/skills", () => ({
  listSkills: vi.fn().mockResolvedValue({
    items: [
      rec({
        name: "pptx",
        description: "Build slide decks",
        category: "office",
        source: "tenant",
      }),
    ],
    platform_items: [
      rec({
        name: "sql-analyst",
        description: "Query databases",
        category: "data",
        source: "platform",
        entitled: true,
      }),
      rec({
        name: "premium-x",
        description: "Locked capability",
        category: "pro",
        source: "platform",
        entitled: false,
        required_tier: "enterprise",
      }),
    ],
    next_cursor: null,
    cross_tenant: false,
  }),
}));

const SEED: AgentManifest = {
  apiVersion: "helix/v1",
  kind: "Agent",
  metadata: { name: "bot" },
  spec: {},
};

describe("SkillPicker", () => {
  it("renders each skill with description, source and category", async () => {
    render(<SkillPicker formData={SEED} onChange={vi.fn()} />);
    expect(await screen.findByText("Build slide decks")).toBeInTheDocument();
    expect(screen.getByText("Query databases")).toBeInTheDocument();
    expect(screen.getByText("office")).toBeInTheDocument();
    // both a platform and a tenant badge are present
    expect(screen.getAllByText(/平台|Platform/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/租户|Tenant/).length).toBeGreaterThan(0);
  });

  it("checking a skill emits it into spec.skills", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SkillPicker formData={SEED} onChange={onChange} />);
    const check = await screen.findByTestId("af-skill-check-pptx");
    await user.click(check);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.skills).toEqual(["pptx"]);
  });

  it("a tier-locked platform skill cannot be checked", async () => {
    render(<SkillPicker formData={SEED} onChange={vi.fn()} />);
    const locked = await screen.findByTestId("af-skill-check-premium-x");
    expect(locked).toBeDisabled();
  });

  it("an already-selected skill stays checked even when not in the list", async () => {
    const seeded: AgentManifest = {
      ...SEED,
      spec: { skills: ["hand-added"] },
    };
    render(<SkillPicker formData={seeded} onChange={vi.fn()} />);
    const check = await screen.findByTestId("af-skill-check-hand-added");
    expect(check).toBeChecked();
  });

  it("unchecking a selected skill removes it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const seeded: AgentManifest = { ...SEED, spec: { skills: ["pptx"] } };
    render(<SkillPicker formData={seeded} onChange={onChange} />);
    const check = await screen.findByTestId("af-skill-check-pptx");
    await user.click(check);
    const last = onChange.mock.calls.at(-1)?.[0] as AgentManifest;
    expect(last.spec?.skills).toBeUndefined();
  });
});
