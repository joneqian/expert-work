"""Per-sandbox egress identity token — minted outside the sandbox, verified at
the egress proxy (sandbox-egress design §3.2).

The sandbox cannot be trusted to self-report which agent it is (the credential
proxy's ``X-Helix-*`` headers are sandbox-supplied). For the transparent egress
proxy we instead inject — at ``docker run``, outside the sandbox — a short,
HMAC-signed token bound to ``(tenant, agent, version, sandbox)``. The proxy
verifies the HMAC with a shared secret (no DB round-trip) and trusts the bound
identity for audit attribution + the optional host allowlist.

The token is NOT a secret kept from skill code (it lives in the sandbox's env so
``HTTPS_PROXY`` works); it only authorizes what *that* agent may already do, so
leaking it to the skill is not a privilege escalation. ``exp`` bounds its life.

Format (compact, URL-safe, no JWT dependency)::

    v1.<b64url(payload_json)>.<b64url(hmac_sha256(secret, "v1." + payload_b64))>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

_VERSION = "v1"


@dataclass(frozen=True)
class EgressIdentity:
    """The agent identity bound into an egress token."""

    tenant_id: str
    agent_name: str
    agent_version: str
    sandbox_id: str
    expires_at: float
    #: Optional per-agent host allowlist (sandbox-egress §3.1 Phase 2). Empty →
    #: any public host allowed (audited). Non-empty → only these hosts (exact or
    #: subdomain) pass; embedded in the signed token so it can't be tampered.
    allowlist: tuple[str, ...] = ()
    #: Optional per-agent host denylist. A host matching an entry (exact or
    #: subdomain) is blocked even when the allowlist would allow it — so an
    #: operator can carve a few bad hosts out of the default allow-all without
    #: enumerating every permitted host. Also embedded in the signed token.
    denylist: tuple[str, ...] = ()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, signing_input: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return _b64url_encode(mac.digest())


def mint_egress_token(
    secret: str,
    *,
    tenant_id: str,
    agent_name: str,
    agent_version: str,
    sandbox_id: str,
    expires_at: float,
    allowlist: tuple[str, ...] = (),
    denylist: tuple[str, ...] = (),
) -> str:
    """Mint a signed egress token. ``expires_at`` is an absolute epoch second
    (the caller supplies the clock — keeps this pure/testable). ``allowlist`` /
    ``denylist`` (optional) embed the per-agent host policy the proxy enforces."""
    if not secret:
        msg = "egress token secret must not be empty"
        raise ValueError(msg)
    payload: dict[str, object] = {
        "t": tenant_id,
        "a": agent_name,
        "v": agent_version,
        "s": sandbox_id,
        "exp": expires_at,
    }
    if allowlist:
        payload["al"] = list(allowlist)
    if denylist:
        payload["dl"] = list(denylist)
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{_VERSION}.{payload_b64}"
    return f"{signing_input}.{_sign(secret, signing_input)}"


def _host_matches(host: str, entries: tuple[str, ...]) -> bool:
    """Whether ``host`` exactly equals, or is a subdomain of, any entry.
    ``["openai.com"]`` matches ``openai.com`` and ``api.openai.com`` but not
    ``evilopenai.com``. Empty ``entries`` → no match."""
    h = host.rstrip(".").lower()
    for entry in entries:
        e = entry.rstrip(".").lower()
        if e and (h == e or h.endswith(f".{e}")):
            return True
    return False


def host_in_allowlist(host: str, allowlist: tuple[str, ...]) -> bool:
    """Whether ``host`` is permitted by ``allowlist`` (empty → allow all)."""
    if not allowlist:
        return True
    return _host_matches(host, allowlist)


def host_in_denylist(host: str, denylist: tuple[str, ...]) -> bool:
    """Whether ``host`` is blocked by ``denylist`` (empty → blocks nothing).

    Takes precedence over the allowlist: a denied host is refused even when the
    allowlist (or the default allow-all) would let it through."""
    if not denylist:
        return False
    return _host_matches(host, denylist)


def verify_egress_token(secret: str, token: str, *, now: float) -> EgressIdentity | None:
    """Verify an egress token's signature and expiry against ``now`` (epoch s).

    Returns the bound :class:`EgressIdentity`, or ``None`` if the token is
    malformed, the signature does not match, or it has expired. Never raises on
    bad input — a bad token is just an unauthenticated caller."""
    if not secret or not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        return None
    version, payload_b64, signature = parts
    expected = _sign(secret, f"{version}.{payload_b64}")
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        expires_at = float(payload["exp"])
        raw_allowlist = payload.get("al") or []
        raw_denylist = payload.get("dl") or []
        identity = EgressIdentity(
            tenant_id=str(payload["t"]),
            agent_name=str(payload["a"]),
            agent_version=str(payload["v"]),
            sandbox_id=str(payload["s"]),
            expires_at=expires_at,
            allowlist=tuple(str(h) for h in raw_allowlist),
            denylist=tuple(str(h) for h in raw_denylist),
        )
    except (ValueError, KeyError, TypeError):
        return None
    if now >= expires_at:
        return None
    return identity
