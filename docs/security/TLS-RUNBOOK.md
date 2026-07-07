# TLS Runbook (M0)

Operator-facing procedure for issuing, deploying, and rotating the M0
static mTLS bundle. Implements the M0 row of
[subsystems/28-reliability-primitives § 5.1](../architecture/subsystems/28-reliability-primitives.md#51-tls-端到端策略).

> **M0 constraint:** static 7-day certs, manual rotation. This is **documented
> technical debt** — M1 replaces it with cert-manager + SPIRE for automatic
> 1-hour rotation. Reason: single-operator project can't justify a full PKI
> automation stack yet.

## Initial bundle generation

```bash
# From the repo root
./tools/tls/gen-mtls-bundle.sh ./tls-bundle
```

Default produces **CA + 3 leaf certs** for ``control_plane``,
``orchestrator``, ``sandbox_supervisor``. Add more services as positional
args:

```bash
./tools/tls/gen-mtls-bundle.sh ./tls-bundle credential_proxy mcp_gateway
```

Output layout:

```
tls-bundle/
├── ca.crt                       # distribute to every peer's trust store
├── ca.key                       # KEEP OFFLINE — move out of repo after dev
├── control_plane.{key,crt}      # one cert per service
├── orchestrator.{key,crt}
├── sandbox_supervisor.{key,crt}
└── ca.srl                       # CA serial counter (committed-on-rotation)
```

Permissions: ``*.key`` files are ``chmod 600`` automatically; verify
before committing or shipping that **no .key file** is in git.

## Verifying a leaf cert

```bash
openssl verify -CAfile tls-bundle/ca.crt tls-bundle/control_plane.crt
openssl x509 -in tls-bundle/control_plane.crt -noout -text \
    | grep -E "(Subject:|DNS:|Not (Before|After))"
```

Expected output:

```
Subject: CN = control_plane.expert_work.local, O = Expert Work
DNS:control_plane.expert_work.local, DNS:localhost, IP Address:127.0.0.1
Not Before: <today>
Not After : <today + 7 days>
```

## Deploying to staging / prod

1. Generate on a secured workstation (laptop with full-disk encryption, no
   shared filesystem).
2. Copy ``ca.crt`` + ``<service>.{key,crt}`` to ``/etc/expert-work/tls/``
   on each node via ``scp`` (never via S3 / OSS unless server-side encrypted).
3. Set ``EXPERT_WORK_TLS_DIR=/etc/expert-work/tls`` in the systemd unit.
4. Confirm via the application's ``/healthz/ready`` — TLS handshake errors
   surface in the dep-check list (Stream A.11).

## Rotation (every 6 days)

Cert lifetime is **7 days**. Rotate on day 6:

```bash
# 1. Regenerate. Existing ca.crt / ca.key are reused if present, so peer
#    trust stores DON'T need updating between rotations.
./tools/tls/gen-mtls-bundle.sh /etc/expert-work/tls

# 2. Reload each service. Graceful Lifecycle SIGHUP support lands in
#    Stream B; until then, rolling restart via the lifecycle's drain path.
systemctl reload expert-work-control-plane
systemctl reload expert-work-orchestrator
systemctl reload expert-work-sandbox-supervisor
```

**Calendar reminder:** set a 6-day recurring reminder. Failing to rotate
breaks every mTLS connection within 24 h of expiry and the alert in
subsystems/28 § 7.4 (``TLSCertExpiringSoon``) fires at the 48h mark.

## Re-issuing the CA

Only required when:

- The CA private key may have been disclosed (e.g., laptop loss)
- Migrating to M1 cert-manager (one-time)

Steps:

```bash
# 1. Wipe the old bundle (including ca.key) and regenerate from scratch.
rm -rf /etc/expert-work/tls/{*.key,*.crt,*.srl}
./tools/tls/gen-mtls-bundle.sh /etc/expert-work/tls

# 2. Distribute the new ca.crt to every peer's trust store *before* the
#    next handshake. For the M0 mesh, that's the same three services.
#
# 3. Rolling restart everything.
```

## Known M0 limitations

- **No automated rotation** — the calendar reminder is the only line of
  defence. M1 cert-manager closes this.
- **No revocation list / CRL** — once a cert is issued, it's valid for
  7 days. Compromise mitigation: re-issue the CA (above).
- **Single CA for every environment** — dev / staging / prod use the
  same script with different paths. M1 splits per environment.
- **No SPIFFE identities** — service identity is encoded in ``CN`` only;
  cross-environment cert reuse is not detected. M1 SPIRE fixes this.

## CI gates

The repo lint pipeline runs
[``tools/tls/check_tls_config.py``](../../tools/tls/check_tls_config.py)
to enforce ``tls.min_version >= 1.2`` across ``environments/*.yaml``.
Anyone PRing a lower min version (or omitting the field on a populated
``tls`` block) gets caught at the Lint job.
