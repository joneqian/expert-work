import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "../../i18n";

vi.mock("@monaco-editor/react", () => {
  const Editor = ({
    value,
    onChange,
  }: {
    value?: string;
    onChange?: (v: string | undefined) => void;
  }) => (
    <textarea
      data-testid="monaco-stub"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  );
  return { default: Editor };
});

const mockNavigate = vi.fn();
vi.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock("../../api/platform_embedding_config", () => ({
  getPlatformEmbeddingStatus: vi.fn(),
}));

import * as agentsSdk from "../../api/agents";
import * as schemaSdk from "../../api/manifest_schema";
import * as catalogSdk from "../../api/model_catalog";
import { getPlatformEmbeddingStatus } from "../../api/platform_embedding_config";
import { __resetSchemaCacheForTest } from "../manifest-editor/schema";
import { __resetCatalogCacheForTest } from "../manifest-editor/catalog";
import { ApiError } from "../../api/client";
import { CreateAgentDrawer } from "../CreateAgentDrawer";

const sampleCreated = {
  record: { name: "my-agent", version: "1.0.0" },
} as unknown as agentsSdk.AgentDetailResponse;

const onClose = vi.fn();
const onCreated = vi.fn();

beforeEach(() => {
  __resetSchemaCacheForTest();
  vi.spyOn(schemaSdk, "fetchAgentSchema").mockResolvedValue({
    type: "object",
    properties: { metadata: { type: "object", properties: { name: { type: "string" } } } },
  });
  __resetCatalogCacheForTest();
  vi.spyOn(catalogSdk, "fetchModelCatalog").mockResolvedValue({
    providers: [
      {
        provider: "deepseek",
        models: [
          {
            name: "deepseek-v4-pro",
            vision: false,
            embeddings: false,
            context_window: 1000000,
            deprecated: false,
          },
        ],
      },
    ],
  });
  vi.mocked(getPlatformEmbeddingStatus).mockResolvedValue({ configured: true });
  mockNavigate.mockClear();
  onClose.mockClear();
  onCreated.mockClear();
});
afterEach(() => vi.restoreAllMocks());

describe("CreateAgentDrawer", () => {
  it("renders the manifest editor on the Form tab", async () => {
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await waitFor(() => expect(screen.getByTestId("manifest-editor-create")).toBeInTheDocument());
    expect(screen.getByTestId("manifest-form-view")).toBeInTheDocument();
  });

  it("submits the default manifest YAML via createAgent", async () => {
    const user = userEvent.setup();
    const createMock = vi.spyOn(agentsSdk, "createAgent").mockResolvedValue(sampleCreated);
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");

    await user.click(screen.getByTestId("create-agent-submit"));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0][0] as { manifest_yaml: string };
    expect(payload.manifest_yaml).toContain("kind: Agent");
    expect(onCreated).toHaveBeenCalledWith(sampleCreated);
  });

  it("surfaces server errors and keeps the drawer open", async () => {
    const user = userEvent.setup();
    vi.spyOn(agentsSdk, "createAgent").mockRejectedValue(
      new ApiError("name + version already exists", "MANIFEST_DUPLICATE", 409),
    );
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");
    await user.click(screen.getByTestId("create-agent-submit"));
    const alert = await screen.findByTestId("create-agent-error");
    expect(alert).toHaveTextContent("MANIFEST_DUPLICATE");
  });

  it("seeds the editor with the first configured provider's model", async () => {
    const user = userEvent.setup();
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);
    await screen.findByTestId("manifest-editor-create");
    await user.click(screen.getByTestId("manifest-tab-yaml"));
    const ta = screen.getByTestId("monaco-stub") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toContain("provider: deepseek"));
  });

  it("blocks creation and shows the gate when platform embedding is unconfigured", async () => {
    vi.mocked(getPlatformEmbeddingStatus).mockResolvedValue({ configured: false });
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);

    await screen.findByTestId("create-agent-embedding-gate");
    expect(screen.queryByTestId("manifest-editor-create")).not.toBeInTheDocument();
    expect(screen.getByTestId("create-agent-submit")).toBeDisabled();
  });

  it("does not show the gate when platform embedding is configured", async () => {
    vi.mocked(getPlatformEmbeddingStatus).mockResolvedValue({ configured: true });
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);

    await screen.findByTestId("manifest-editor-create");
    await waitFor(() =>
      expect(screen.queryByTestId("create-agent-embedding-gate")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("create-agent-submit")).toBeEnabled();
  });

  it("navigates to platform settings and closes when the gate CTA is clicked", async () => {
    const user = userEvent.setup();
    vi.mocked(getPlatformEmbeddingStatus).mockResolvedValue({ configured: false });
    render(<CreateAgentDrawer open onClose={onClose} onCreated={onCreated} />);

    await screen.findByTestId("create-agent-embedding-gate");
    await user.click(screen.getByTestId("create-agent-embedding-cta"));

    expect(mockNavigate).toHaveBeenCalledWith("/settings/platform");
    expect(onClose).toHaveBeenCalled();
  });
});
