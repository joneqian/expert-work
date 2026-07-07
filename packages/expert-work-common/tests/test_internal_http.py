"""Unit tests for :mod:`expert_work.common.internal_http`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from expert_work.common.internal_http import (
    DEFAULT_TIMEOUT_S,
    InternalHttpConfigError,
    build_internal_http_client,
    build_internal_http_sync_client,
)


@lru_cache(maxsize=1)
def _real_pki() -> tuple[bytes, bytes, bytes]:
    """Generate one self-signed CA + client cert+key for the whole module.

    ``httpx`` validates the CA bundle path at client construction (via
    ``ssl.create_default_context(cafile=...)``) so the fixture has to
    hand back real PEM material.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expert-work-test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem, cert_pem  # cert acts as its own CA bundle


@pytest.fixture
def cert_triple(tmp_path: Path) -> tuple[Path, Path, Path]:
    cert_pem, key_pem, ca_pem = _real_pki()
    cert = tmp_path / "client.crt"
    cert.write_bytes(cert_pem)
    key = tmp_path / "client.key"
    key.write_bytes(key_pem)
    ca = tmp_path / "ca.crt"
    ca.write_bytes(ca_pem)
    return cert, key, ca


def test_async_factory_validates_paths(cert_triple: tuple[Path, Path, Path]) -> None:
    cert, key, ca = cert_triple
    client = build_internal_http_client(
        base_url="https://control-plane.expert_work",
        client_cert_path=cert,
        client_key_path=key,
        ca_bundle_path=ca,
    )
    assert isinstance(client, httpx.AsyncClient)
    # Structured assert avoids ``str(...).startswith`` which CodeQL flags as
    # ``py/incomplete-url-substring-sanitization`` (substring URL checks can
    # be bypassed by attacker-controlled suffixes).
    assert client.base_url.scheme == "https"
    assert client.base_url.host == "control-plane.expert_work"


def test_sync_factory_validates_paths(cert_triple: tuple[Path, Path, Path]) -> None:
    cert, key, ca = cert_triple
    client = build_internal_http_sync_client(
        base_url="https://control-plane.expert_work",
        client_cert_path=cert,
        client_key_path=key,
        ca_bundle_path=ca,
    )
    assert isinstance(client, httpx.Client)


def test_missing_cert_raises_config_error(
    cert_triple: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    _, key, ca = cert_triple
    with pytest.raises(InternalHttpConfigError, match="client cert"):
        build_internal_http_client(
            base_url="https://control-plane.expert_work",
            client_cert_path=tmp_path / "missing.crt",
            client_key_path=key,
            ca_bundle_path=ca,
        )


def test_missing_key_raises_config_error(
    cert_triple: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cert, _, ca = cert_triple
    with pytest.raises(InternalHttpConfigError, match="client key"):
        build_internal_http_client(
            base_url="https://x",
            client_cert_path=cert,
            client_key_path=tmp_path / "missing.key",
            ca_bundle_path=ca,
        )


def test_missing_ca_raises_config_error(
    cert_triple: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    cert, key, _ = cert_triple
    with pytest.raises(InternalHttpConfigError, match="CA bundle"):
        build_internal_http_client(
            base_url="https://x",
            client_cert_path=cert,
            client_key_path=key,
            ca_bundle_path=tmp_path / "missing.crt",
        )


def test_default_timeout_applied(cert_triple: tuple[Path, Path, Path]) -> None:
    cert, key, ca = cert_triple
    client = build_internal_http_client(
        base_url="https://x",
        client_cert_path=cert,
        client_key_path=key,
        ca_bundle_path=ca,
    )
    # httpx exposes the timeout via the public ``timeout`` attribute.
    assert client.timeout.connect == pytest.approx(DEFAULT_TIMEOUT_S)
