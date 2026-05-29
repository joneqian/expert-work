# Agent manifests

Checked-in `AgentSpec` manifests (YAML). Authored against the live schema in
`packages/helix-protocol/src/helix_agent/protocol/agent_spec.py` and loaded by
`services/control-plane/src/control_plane/manifest/loader.py`.

## canonical-agent

`canonical-agent/v1.0.0.yaml` — the full-capability reference agent the
M0→M1 Gate end-to-end test (`docs/runbooks/canonical-agent-e2e-test.md`)
registers and runs through Phases 1-6 (long-term memory, persistent
workspace, human-approval gate, multimodal vision). Kept loadable by
`services/control-plane/tests/test_canonical_manifest.py`.

### Register it

The provider key is a `secret://` ref — set up the dev key first
(`docs/runbooks/bootstrap-admin.md` § 5).

```sh
# As a logged-in tenant user (Bearer token):
curl -sS -X POST http://localhost:8000/v1/agents \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "$(jq -Rn --rawfile y manifests/canonical-agent/v1.0.0.yaml '{manifest: $y}')"
```

Or in the Admin UI: **New Agent → Upload YAML → select the file**.
