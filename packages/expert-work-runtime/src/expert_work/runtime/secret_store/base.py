"""SecretStore abstraction — Stream F.6, realising ADR-0007.

All application code reads secrets (LLM provider API keys, webhook
secrets, …) through the :class:`SecretStore` Protocol — never a cloud
SDK directly — so the backend is a swap-in adapter (ADR-0007 § 2.2):

- :class:`~expert_work.runtime.secret_store.local_dev.LocalDevSecretStore`
  — dev / test, ``.env``-file backed, zero external dependency.
- ``AliyunKmsSecretStore`` — M0 production backend (阿里云 KMS Secrets
  Manager). A follow-up; the :func:`make_secret_store` factory raises
  ``NotImplementedError`` for it until then, mirroring how
  ``stream_bridge`` defers its Redis backend.

Manifests / tenant configs reference a secret by a ``secret://`` URI
(see :mod:`expert_work.runtime.secret_store.refs`); the URI's path is
the opaque ``name`` passed to :meth:`SecretStore.get`.

The package directory is ``secret_store/`` rather than ADR-0007's
originally-written ``secrets/`` — the latter trips tooling that assumes
a ``secrets/`` directory holds credential *values*; this package is
abstraction *code*. ADR-0007 § 2.2 / § 5 are amended to match.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class SecretStoreError(Exception):
    """Base class for secret-store failures."""


class SecretNotFoundError(SecretStoreError, KeyError):
    """No secret is registered under the requested name.

    Subclasses :class:`KeyError` so callers may catch it with the
    intuitive ``except KeyError`` as well as the explicit type.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"secret not found: {name!r}")
        self.name = name


@runtime_checkable
class SecretStore(Protocol):
    """Async read/write surface for application secrets (ADR-0007 § 2.2).

    Every backend (local-dev, Aliyun KMS, future Vault) implements this;
    application code depends only on the Protocol. Methods are ``async``
    because production backends are network-bound — even though
    :class:`LocalDevSecretStore` resolves in memory.
    """

    async def get(self, name: str, *, version: str | None = None) -> str:
        """Return the secret value for ``name``.

        ``version`` selects a specific version when the backend supports
        versioning (``None`` → latest). Raises :class:`SecretNotFoundError`
        if no such secret / version exists.
        """

    async def put(self, name: str, value: str) -> None:
        """Create or update the secret ``name`` — admin / bootstrap only.

        Production callers rarely write; this exists for dev seeding and
        rotation tooling.
        """

    async def list_versions(self, name: str) -> list[str]:
        """Return the known version identifiers for ``name``, newest first.

        Backends without versioning return a single synthetic id. Raises
        :class:`SecretNotFoundError` if ``name`` is unknown.
        """
