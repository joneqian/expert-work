# Keycloak — local dev realm

This directory holds the Keycloak realm that backs **Stream C.1** in local
development.

## What's here

- `realm-expert-work.json` — single-realm export imported on Keycloak
  startup (`docker-compose up keycloak`). Contains:
  - Realm `expert-work`
  - Realm roles: `admin` / `operator` / `viewer`
  - Public client `expert-work-admin-ui` (Authorization Code + PKCE, for
    the future M0 admin UI)
  - Confidential client `expert-work-api-internal` with Service Account
    enabled (for Stream C.2 / C.3 internal service-to-service calls and
    integration tests)
  - User attribute mapper that copies `tenant_id` into the access token's
    top-level claims
  - Dev user `dev@expert-work.local` (password `devpass`, role `admin`, tenant
    `00000000-0000-0000-0000-000000000000`)

## Boot

```bash
cd infra && docker compose up -d keycloak
# Realm import takes ~20s on first boot. Watch:
docker compose logs -f keycloak | grep "Imported realm"
```

Admin console: <http://localhost:8080/> (login `admin` / `admin_dev`).

## Where the control plane reads it

`environments/dev.yaml` will set:

```yaml
EXPERT_WORK_OIDC_ISSUER: "http://keycloak:8080/realms/expert-work"
EXPERT_WORK_OIDC_AUDIENCE: ["expert-work-api-internal"]
```

The control plane fetches JWKS from
`{issuer}/protocol/openid-connect/certs` and caches it for
`EXPERT_WORK_OIDC_JWKS_CACHE_TTL_S` seconds (default 300).

## Why a single dev user is enough for C.1

Stream C.1 only ships JWT verification — it does not log users in or
manage them. Integration tests sign JWTs with a synthetic keypair via
`tests/auth_fixtures.py`; the Keycloak container is only here for manual
sanity checks and as a staging step for the C.3 / C.4 / C.5 PRs that
follow.

## Prod

The same realm JSON is imported into production Keycloak via the admin
CLI. Diff-against-prod runs in CI to flag drift; see the prod realm
runbook (lands in M1).
