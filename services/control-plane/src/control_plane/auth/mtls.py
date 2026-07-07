"""mTLS / X-Forwarded-Client-Cert (XFCC) verifier — Stream C.2.

Service-to-service authentication path. TLS termination happens at the
reverse proxy (nginx in dev, Istio sidecar / Envoy in prod). The proxy
verifies the client certificate, then forwards a structured header that
identifies the peer to the control plane.

We use the **Envoy XFCC format** (Istio + Envoy + most ingress proxies
emit it); per the Envoy spec the header carries one or more comma-
separated elements, each a ``;``-separated key=value list:

.. code:: text

   X-Forwarded-Client-Cert: By=spiffe://...;Hash=abc123;Subject="CN=foo,O=expert_work";URI=spiffe://foo

We only need ``Subject`` (DN) and ``Hash`` (sha256 hex) — the rest is
informational.

A verified peer becomes a :class:`Principal` with ``subject_type="service"``
and ``tenant_id == settings.mtls_system_tenant_id`` (a sentinel UUID,
distinct from any real tenant). Internal handlers (e.g. ``/v1/quota/*``
that ships in C.5) must read the *target* tenant from the request body —
mTLS only proves *who is calling*, not *who they're calling for*.

ADR C-2 (``docs/streams/STREAM-C-DESIGN.md``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from control_plane.auth.errors import InvalidTokenError
from expert_work.protocol import Principal

logger = logging.getLogger("expert_work.control_plane.auth.mtls")

# Pattern that matches one XFCC key=value pair where the value is either a
# quoted string (allowing internal ``,`` and ``;``) or a bare token.
_XFCC_PAIR = re.compile(
    r"""
    (?P<key>[A-Za-z][A-Za-z0-9_-]*)   # key
    =
    (?:
        "(?P<qval>[^"\\]*(?:\\.[^"\\]*)*)"   # quoted value
        |
        (?P<bval>[^;,]+)                     # bare value (no ; or ,)
    )
    """,
    re.VERBOSE,
)

# Pattern to lift the CN out of an RFC-2253 / OpenSSL-style DN. Tolerates
# both ``CN=foo,O=bar`` (RFC) and ``/CN=foo/O=bar`` (openssl) forms.
_CN_RFC = re.compile(r"(?:^|,\s*)CN\s*=\s*([^,]+)", re.IGNORECASE)
_CN_OPENSSL = re.compile(r"/CN\s*=\s*([^/]+)", re.IGNORECASE)


@dataclass(frozen=True)
class XfccElement:
    """One parsed XFCC list element. Empty fields render as ``""``."""

    subject_dn: str = ""
    sha256: str = ""
    uri: str = ""

    @property
    def common_name(self) -> str:
        if not self.subject_dn:
            return ""
        # Try RFC form first (most proxies emit this), fall back to OpenSSL.
        match = _CN_RFC.search(self.subject_dn) or _CN_OPENSSL.search(self.subject_dn)
        if match is None:
            return ""
        return match.group(1).strip().strip('"')


def parse_xfcc_header(header_value: str) -> list[XfccElement]:
    """Parse one ``X-Forwarded-Client-Cert`` header into structured elements.

    Returns an empty list if the header is empty or malformed. Never
    raises — callers decide how to react to "no elements".
    """
    if not header_value:
        return []
    elements: list[XfccElement] = []
    for raw in _split_xfcc_list(header_value):
        chunk = raw.strip()
        if not chunk:
            continue
        subject = ""
        sha256 = ""
        uri = ""
        for match in _XFCC_PAIR.finditer(chunk):
            key = match.group("key").lower()
            value = match.group("qval")
            if value is None:
                value = (match.group("bval") or "").strip()
            else:
                # Un-escape the only escape XFCC permits (``\"``).
                value = value.replace('\\"', '"')
            if key == "subject":
                subject = value
            elif key == "hash":
                sha256 = value
            elif key == "uri":
                uri = value
        if subject or sha256 or uri:
            elements.append(XfccElement(subject_dn=subject, sha256=sha256, uri=uri))
    return elements


def _split_xfcc_list(header_value: str) -> list[str]:
    """Split a top-level XFCC list on ``,`` while respecting quoted strings."""
    parts: list[str] = []
    depth = 0  # for quoted-string tracking
    in_quotes = False
    escape = False
    buffer: list[str] = []
    for char in header_value:
        if escape:
            buffer.append(char)
            escape = False
            continue
        if char == "\\" and in_quotes:
            buffer.append(char)
            escape = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            buffer.append(char)
            continue
        if char == "," and not in_quotes and depth == 0:
            parts.append("".join(buffer))
            buffer.clear()
            continue
        buffer.append(char)
    parts.append("".join(buffer))
    return parts


@dataclass(frozen=True)
class MTLSVerifier:
    """Translate a verified peer cert (proxied via XFCC) into a :class:`Principal`.

    The reverse proxy is responsible for the cryptographic validation —
    by the time we see the header, the cert *signed by our CA* has been
    confirmed. Our job is:

    1. Parse XFCC
    2. Confirm the subject's CN is in the allowlist
    3. Mint a service-typed Principal anchored to the configured system
       tenant
    """

    allowed_subjects: tuple[str, ...]
    system_tenant_id: UUID
    require_uri_san: bool = False

    def verify(self, xfcc_header: str) -> Principal:
        elements = parse_xfcc_header(xfcc_header)
        if not elements:
            raise InvalidTokenError()
        # In M0 we only consult the first element. Proxies append upstream
        # certs to the list when chaining; the immediate peer is element 0.
        peer = elements[0]
        cn = peer.common_name
        if not cn:
            raise InvalidTokenError()
        # Opt-in security: an empty allowlist blocks everyone. ``settings``
        # ships a non-empty default; tests can clear it to lock the door.
        if cn not in self.allowed_subjects:
            logger.info("mtls.subject_not_allowed", extra={"subject_cn": cn})
            raise InvalidTokenError()
        if self.require_uri_san and not peer.uri:
            raise InvalidTokenError()

        return Principal(
            subject_id=cn,
            subject_type="service",
            tenant_id=self.system_tenant_id,
            roles=("service",),
            scopes=(),
            auth_method="mtls",
            allowed_tenants=(self.system_tenant_id,),
        )


def build_mtls_verifier(
    *,
    allowed_subjects: Sequence[str] | Iterable[str],
    system_tenant_id: UUID,
    require_uri_san: bool = False,
) -> MTLSVerifier:
    return MTLSVerifier(
        allowed_subjects=tuple(allowed_subjects),
        system_tenant_id=system_tenant_id,
        require_uri_san=require_uri_san,
    )
