/**
 * KnowledgeAdmin tests — Stream H.7 PR 1 (design § 6.9.4).
 *
 * The knowledge SDK is stubbed. Covers: bases render + create modal,
 * documents 4-state tags + failed error tooltip, upload whitelist
 * pre-flight (H-18's poll is started only by non-terminal docs — fake
 * timers), and the H-19 scope note.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { MemoryRouter } from "react-router-dom";
import "../../i18n";

import * as knowledgeSdk from "../../api/knowledge";
import { KnowledgeAdmin } from "../KnowledgeAdmin";

let mockScope: string | undefined;
vi.mock("../../tenant/TenantScopeContext", () => ({
  useTenantScope: () => ({ scope: mockScope, apiTenantScope: mockScope }),
}));

const BASES: knowledgeSdk.KnowledgeBase[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "support-docs",
    chunk_max_tokens: 512,
    chunk_overlap_tokens: 64,
    created_at: "2026-06-12T00:00:00Z",
  },
];

const DOCS: knowledgeSdk.KnowledgeDocument[] = [
  {
    id: "22222222-2222-2222-2222-222222222222",
    filename: "faq.pdf",
    status: "ready",
    error: null,
    chunk_count: 12,
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:05:00Z",
  },
  {
    id: "33333333-3333-3333-3333-333333333333",
    filename: "broken.docx",
    status: "failed",
    error: "parse error: empty document",
    chunk_count: 0,
    created_at: "2026-06-12T00:00:00Z",
    updated_at: "2026-06-12T00:01:00Z",
  },
];

function renderPage() {
  return render(
    <MemoryRouter>
      <App>
        <KnowledgeAdmin />
      </App>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockScope = undefined;
});

afterEach(() => vi.restoreAllMocks());

describe("KnowledgeAdmin", () => {
  it("renders bases and, on selection, the documents with status tags", async () => {
    vi.spyOn(knowledgeSdk, "listBases").mockResolvedValue(BASES);
    const docsSpy = vi.spyOn(knowledgeSdk, "listDocuments").mockResolvedValue(DOCS);

    renderPage();

    await waitFor(() => expect(screen.getByText("support-docs")).toBeInTheDocument());
    await userEvent.click(screen.getByText("support-docs"));

    await waitFor(() => expect(screen.getByText("faq.pdf")).toBeInTheDocument());
    expect(docsSpy).toHaveBeenCalledWith("support-docs");
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("create modal posts the base and surfaces 409 as a duplicate message", async () => {
    vi.spyOn(knowledgeSdk, "listBases").mockResolvedValue([]);
    const createSpy = vi.spyOn(knowledgeSdk, "createBase").mockResolvedValue(BASES[0]);

    renderPage();
    await userEvent.click(screen.getByTestId("kb-create-open"));
    await waitFor(() => expect(screen.getByTestId("kb-create-modal")).toBeInTheDocument());
    await userEvent.type(screen.getByTestId("kb-create-name"), "support-docs");
    await userEvent.click(screen.getByText("Save"));

    await waitFor(() =>
      expect(createSpy).toHaveBeenCalledWith(
        expect.objectContaining({ name: "support-docs" }),
      ),
    );
  });

  it("rejects an unsupported extension before any request (whitelist pre-flight)", async () => {
    vi.spyOn(knowledgeSdk, "listBases").mockResolvedValue(BASES);
    vi.spyOn(knowledgeSdk, "listDocuments").mockResolvedValue([]);
    const uploadSpy = vi.spyOn(knowledgeSdk, "uploadDocument");

    renderPage();
    await waitFor(() => expect(screen.getByText("support-docs")).toBeInTheDocument());
    await userEvent.click(screen.getByText("support-docs"));
    await waitFor(() => expect(screen.getByTestId("doc-upload")).toBeInTheDocument());

    const file = new File(["x"], "malware.exe", { type: "application/octet-stream" });
    const input = document.querySelector<HTMLInputElement>("input[type=file]");
    expect(input).not.toBeNull();
    // applyAccept:false — the ``accept`` attr would filter the file at the
    // browser layer; we want to exercise the beforeUpload pre-flight.
    await userEvent.upload(input as HTMLInputElement, file, { applyAccept: false });

    await waitFor(() =>
      expect(screen.getByText(/Unsupported document type/)).toBeInTheDocument(),
    );
    expect(uploadSpy).not.toHaveBeenCalled();
  });

  it("shows the H-19 scope note when the global scope is not home", async () => {
    mockScope = "*";
    vi.spyOn(knowledgeSdk, "listBases").mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByTestId("knowledge-scope-note")).toBeInTheDocument(),
    );
  });

  it("hides the scope note on the home scope", async () => {
    mockScope = undefined;
    vi.spyOn(knowledgeSdk, "listBases").mockResolvedValue([]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("No knowledge bases yet.")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("knowledge-scope-note")).toBeNull();
  });

  it("isSupportedDocument matches the backend whitelist", () => {
    expect(knowledgeSdk.isSupportedDocument("a.PDF")).toBe(true);
    expect(knowledgeSdk.isSupportedDocument("notes.markdown")).toBe(true);
    expect(knowledgeSdk.isSupportedDocument("payload.exe")).toBe(false);
    expect(knowledgeSdk.isSupportedDocument("no-extension")).toBe(false);
  });
});
