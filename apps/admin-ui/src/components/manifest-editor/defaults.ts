/**
 * Default manifest template + capability-adaptive seeding (Mini-ADR S-5).
 *
 * ``BASE_MANIFEST_YAML`` is the blank-canvas manifest. ``buildDefaultManifest``
 * pre-selects the first *configured* provider's first chat (non-embedding)
 * model and copies its vision capability, so a new agent starts on a model the
 * platform can actually build. Long-term memory is ON by default
 * (Stream T): a memory-less agent has little product value, so new agents seed
 * with ``spec.memory.long_term``. This requires a platform embedding config —
 * CreateAgentModal blocks+guides when none is set (the build-time embedder
 * gate is the backstop).
 */
import { parseYaml } from "./yaml";
import type { CatalogModel, ModelCatalog } from "../../api/model_catalog";

export const BASE_MANIFEST_YAML = `apiVersion: helix.io/v1
kind: Agent
metadata:
  name: my-agent
  version: "1.0.0"
  tenant: my-tenant
spec:
  tenant_config: {}
  model:
    provider: anthropic
    name: claude-sonnet-4-6
  system_prompt:
    template: "You are a helpful assistant."
  memory:
    long_term:
      retrieve_top_k: 5
      write_back: true
      recall_mode: per_session
  sandbox:
    resources: { cpu: "1.0", memory: "1Gi" }
    network:
      egress: proxy
      allowlist: []  # empty = allow all public hosts (SSRF blocked, audited)
      denylist: []   # block these hosts even under allow-all (takes precedence)
    filesystem:
      readonly_root: true
      writable: ["/workspace"]
`;

interface FirstChat {
  provider: string;
  model: CatalogModel;
}

function firstChatModel(catalog: ModelCatalog): FirstChat | null {
  for (const p of catalog.providers) {
    const chat = p.models.find((m) => !m.embeddings && !m.deprecated);
    if (chat) return { provider: p.provider, model: chat };
  }
  return null;
}

export function buildDefaultManifest(catalog: ModelCatalog): unknown {
  const base = parseYaml(BASE_MANIFEST_YAML) as Record<string, unknown>;
  const pick = firstChatModel(catalog);
  if (!pick) return base;
  const spec = base.spec as Record<string, unknown>;
  return {
    ...base,
    spec: {
      ...spec,
      model: {
        provider: pick.provider,
        name: pick.model.name,
        supports_vision: pick.model.vision,
      },
    },
  };
}
