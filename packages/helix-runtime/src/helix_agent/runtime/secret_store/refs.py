"""Secret-reference URI parsing — Stream F.6.

A manifest / tenant-config never embeds a secret *value*; it embeds a
**reference** — a URI that :class:`SecretStore` resolves at runtime.

The canonical scheme is ``secret://<name>``. ``<name>`` is opaque to
the manifest and passed verbatim to :meth:`SecretStore.get`; ADR-0007
§ 2.1 recommends the naming convention ``helix-agent/<env>/<service>/<key>``.

``secret://`` is **backend-agnostic** on purpose — a manifest must not
know whether the backend is KMS, Vault, or a dev ``.env``. Stream C's
design text used a ``kms://`` scheme, which leaks the backend; it is
accepted here as a tolerated alias (resolving to the same name) so
existing Stream C config keeps working, but ``secret://`` is canonical
and ``kms://`` should be migrated.
"""

from __future__ import annotations

#: Canonical reference scheme.
SECRET_SCHEME = "secret://"

#: Tolerated legacy alias (Stream C ``model_credentials_ref`` text).
#: Resolves to the same name; emit-a-warning / migrate at the call site.
_LEGACY_KMS_SCHEME = "kms://"


class InvalidSecretRefError(ValueError):
    """The string is not a well-formed secret reference URI."""


def is_secret_ref(value: str) -> bool:
    """Return whether ``value`` looks like a secret reference URI."""
    return value.startswith((SECRET_SCHEME, _LEGACY_KMS_SCHEME))


def parse_secret_ref(ref: str) -> str:
    """Return the bare secret ``name`` from a ``secret://`` (or ``kms://``) URI.

    ``"secret://helix-agent/dev/llm/anthropic-api-key"`` →
    ``"helix-agent/dev/llm/anthropic-api-key"``.

    Raises :class:`InvalidSecretRefError` for an unknown scheme or an
    empty name — failing loud here beats handing an empty / mis-scoped
    name to the backend.
    """
    for scheme in (SECRET_SCHEME, _LEGACY_KMS_SCHEME):
        if ref.startswith(scheme):
            name = ref[len(scheme) :].strip().strip("/")
            if not name:
                raise InvalidSecretRefError(f"secret ref has empty name: {ref!r}")
            return name
    raise InvalidSecretRefError(
        f"unrecognised secret ref scheme: {ref!r} (expected {SECRET_SCHEME!r})"
    )
