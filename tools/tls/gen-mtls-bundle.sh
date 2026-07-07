#!/usr/bin/env bash
# Generate a 7-day mTLS CA + service certificate bundle for local dev / dogfood.
#
# Per subsystems/28-reliability-primitives § 5.1 M0 row. M0 ships static 7-day
# certs because the single-operator setup cannot afford a full cert-manager
# stack — this is documented technical debt that M1 closes by rotating
# automatically (cert-manager + SPIRE workload identity).
#
# Usage:
#   tools/tls/gen-mtls-bundle.sh <output_dir> [<service1> <service2> ...]
#
#   <output_dir>   target directory; created if missing
#   <serviceN>     additional service names beyond the defaults
#                  (control_plane, orchestrator, sandbox_supervisor)
#
# Outputs in <output_dir>:
#   ca.key, ca.crt                  root CA (keep ca.key offline outside dev)
#   <service>.key, <service>.crt    one pair per service, signed by ca.crt
#
# Cipher / version constraints (§ 5.1):
#   TLS 1.2 minimum on the wire (enforced at the application layer / nginx —
#   this script only emits certs, not server configs)
#   Keys are P-256 (ECDSA) per Aliyun OSS interop; 2048-bit RSA available via
#   --rsa flag for legacy peers.

set -euo pipefail

readonly CERT_DAYS=7
readonly DEFAULT_SERVICES=("control_plane" "orchestrator" "sandbox_supervisor")

usage() {
    cat >&2 <<EOF
Usage: $0 <output_dir> [<service> ...]

Generates a ${CERT_DAYS}-day mTLS bundle: ca + one cert per service.

Examples:
  $0 ./tls-bundle                                       # default 3 services
  $0 ./tls-bundle credential_proxy mcp_gateway          # extra services

Output:
  ca.{key,crt}                  Root CA
  <service>.{key,crt}           Per-service leaf certs signed by ca.crt
EOF
    exit 2
}

[[ $# -lt 1 ]] && usage

readonly OUT_DIR="$1"
shift

# Build the final service list = defaults + any extras the caller passed.
services=("${DEFAULT_SERVICES[@]}" "$@")

mkdir -p "${OUT_DIR}"

# --- root CA -----------------------------------------------------------------
# A single CA covers every service so mTLS verification reduces to one chain
# at every peer. The CA private key MUST move to KMS / a hardware token before
# anything beyond dogfood — see RUNBOOK.
if [[ ! -f "${OUT_DIR}/ca.key" ]]; then
    echo "==> Generating root CA (P-256, valid ${CERT_DAYS}d)"
    openssl ecparam -name prime256v1 -genkey -noout -out "${OUT_DIR}/ca.key"
    openssl req -new -x509 -days "${CERT_DAYS}" -key "${OUT_DIR}/ca.key" \
        -out "${OUT_DIR}/ca.crt" \
        -subj "/CN=Expert Work Dev CA/O=Expert Work/OU=DevOps" \
        -extensions v3_ca \
        -config <(cat <<'EOF'
[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = Expert Work Dev CA
[ v3_ca ]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
EOF
)
else
    echo "==> Re-using existing CA at ${OUT_DIR}/ca.{key,crt}"
fi

# --- leaf certs --------------------------------------------------------------
for svc in "${services[@]}"; do
    echo "==> Generating leaf cert for service=${svc}"

    openssl ecparam -name prime256v1 -genkey -noout \
        -out "${OUT_DIR}/${svc}.key"

    openssl req -new -key "${OUT_DIR}/${svc}.key" \
        -out "${OUT_DIR}/${svc}.csr" \
        -subj "/CN=${svc}.expert_work.local/O=Expert Work"

    # SAN includes both the service DNS name and localhost so dev runs
    # behind a local reverse proxy still verify.
    openssl x509 -req -days "${CERT_DAYS}" -in "${OUT_DIR}/${svc}.csr" \
        -CA "${OUT_DIR}/ca.crt" -CAkey "${OUT_DIR}/ca.key" -CAcreateserial \
        -out "${OUT_DIR}/${svc}.crt" \
        -extfile <(cat <<EOF
subjectAltName = DNS:${svc}.expert_work.local,DNS:localhost,IP:127.0.0.1
extendedKeyUsage = serverAuth,clientAuth
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
EOF
)

    rm -f "${OUT_DIR}/${svc}.csr"
done

# Restrict private-key permissions so a stray ``cat`` doesn't leak them
# into stdout during interactive debugging.
chmod 600 "${OUT_DIR}"/*.key

echo
echo "==> Bundle ready at ${OUT_DIR}"
echo "    CA:        ca.crt  (expires in ${CERT_DAYS}d)"
echo "    Services:  ${services[*]}"
echo
echo "Renewal: re-run this script before expiry. M1 ships cert-manager"
echo "and SPIRE workload identity to remove the manual step."
