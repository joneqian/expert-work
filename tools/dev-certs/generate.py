#!/usr/bin/env python3
"""Generate the dev mTLS PKI for Stream C.2.

Produces, into ``infra/dev-certs/``:

* ``ca.crt`` / ``ca.key``           — self-signed root CA (5y validity)
* ``server.crt`` / ``server.key``   — TLS server cert for nginx (1y)
* ``orchestrator.crt`` / ``.key``   — client cert for the orchestrator
* ``sandbox-supervisor.crt`` / ``.key``
* ``control-plane.crt`` / ``.key``  — used by integration tests that
                                       impersonate the orchestrator

These are **dev-only** materials — the private keys are uncommitted
(``infra/dev-certs/`` is in ``.gitignore``). Re-run this script whenever
the certs expire or a new service is added to ``mtls_allowed_service_subjects``.

Usage::

    python tools/dev-certs/generate.py
    # or
    python -m tools.dev_certs.generate
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "infra" / "dev-certs"

_ORG = "Expert Work (dev)"
_CA_VALIDITY_DAYS = 1825
_LEAF_VALIDITY_DAYS = 365

#: Service identities issued client certs. CN values must match the
#: ``mtls_allowed_service_subjects`` setting on the control plane.
SERVICE_IDENTITIES: tuple[str, ...] = (
    "orchestrator",
    "sandbox-supervisor",
    "control-plane",
)


def _new_keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _build_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _new_keypair()
    now = datetime.now(UTC)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG),
            x509.NameAttribute(NameOID.COMMON_NAME, "expert-work-dev-ca"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=_CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _build_leaf(
    *,
    common_name: str,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    is_server: bool,
    dns_names: tuple[str, ...] = (),
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = _new_keypair()
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, _ORG),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    eku = x509.ExtendedKeyUsage(
        [x509.ExtendedKeyUsageOID.SERVER_AUTH]
        if is_server
        else [x509.ExtendedKeyUsageOID.CLIENT_AUTH]
    )
    san_entries: list[x509.GeneralName] = [x509.DNSName(common_name)]
    san_entries.extend(x509.DNSName(name) for name in dns_names)
    if is_server:
        san_entries.append(x509.IPAddress(IPv4Address("127.0.0.1")))
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=_LEAF_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(eku, critical=False)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    )
    cert = builder.sign(ca_key, hashes.SHA256())
    return key, cert


def _write_pem(path: Path, *, key: rsa.RSAPrivateKey, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key_path = path.with_suffix(".key")
    cert_path = path.with_suffix(".crt")
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate dev mTLS materials.")
    parser.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing certificates",
    )
    args = parser.parse_args(argv)
    out_dir: Path = args.out

    ca_cert_path = out_dir / "ca.crt"
    if ca_cert_path.exists() and not args.force:
        print(f"error: {ca_cert_path} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1

    print(f"generating dev mTLS PKI into {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    ca_key, ca_cert = _build_ca()
    _write_pem(out_dir / "ca", key=ca_key, cert=ca_cert)
    print("  ca.crt / ca.key")

    server_key, server_cert = _build_leaf(
        common_name="control-plane.expert_work.local",
        ca_key=ca_key,
        ca_cert=ca_cert,
        is_server=True,
        dns_names=("localhost", "control-plane", "nginx"),
    )
    _write_pem(out_dir / "server", key=server_key, cert=server_cert)
    print("  server.crt / server.key")

    for service in SERVICE_IDENTITIES:
        client_key, client_cert = _build_leaf(
            common_name=service,
            ca_key=ca_key,
            ca_cert=ca_cert,
            is_server=False,
        )
        _write_pem(out_dir / service, key=client_key, cert=client_cert)
        print(f"  {service}.crt / {service}.key")

    readme = out_dir / "README.txt"
    readme.write_text(
        "Dev mTLS materials generated by tools/dev-certs/generate.py.\n"
        "These are DEV-ONLY — never reuse in production.\n"
    )
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
