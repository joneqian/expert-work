"""SecretStore backend factory — Stream F.6.

Boot code picks a backend by name (from ``environments/{env}.yaml``'s
``secrets.backend`` field) rather than importing a concrete class, so
swapping dev ↔ production is a config change (ADR-0007 § 2.3).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from expert_work.runtime.secret_store.base import SecretStore
from expert_work.runtime.secret_store.local_dev import LocalDevSecretStore

logger = logging.getLogger(__name__)

#: ``secrets.backend`` values. ``local_dev`` is the M0 dev / test
#: backend; ``aliyun_kms`` is the M0 production backend (ADR-0007 § 2.1)
#: and is a follow-up — see :func:`make_secret_store`.
SecretStoreBackend = Literal["local_dev", "aliyun_kms"]


def make_secret_store(
    backend: str = "local_dev",
    *,
    env_file: str | Path | None = None,
) -> SecretStore:
    """Build a :class:`SecretStore` for ``backend``.

    ``backend`` is typed ``str`` (not :data:`SecretStoreBackend`) because
    it arrives from ``environments/{env}.yaml`` — an arbitrary runtime
    string. An unrecognised value raises :class:`ValueError`.

    - ``"local_dev"`` → :class:`LocalDevSecretStore`. ``env_file`` seeds
      it from a ``.env``-style file; omitted → an empty store.
    - ``"aliyun_kms"`` → raises ``NotImplementedError``. The
      :class:`~expert_work.runtime.secret_store.aliyun_kms.AliyunKmsSecretStore`
      adapter + its short-TTL cache *are* implemented (Stream F.6), but
      the factory cannot build one yet: it needs a concrete
      :class:`~expert_work.runtime.secret_store.aliyun_kms.KmsBackend`,
      and the real Aliyun SDK client is a deploy-time follow-up
      (STREAM-F-DESIGN Mini-ADR F-7) — it can only be verified against a
      live Aliyun account. Construct ``AliyunKmsSecretStore(backend)``
      directly once that backend exists.
    """
    if backend == "local_dev":
        store: SecretStore = (
            LocalDevSecretStore.from_env_file(env_file)
            if env_file is not None
            else LocalDevSecretStore()
        )
        logger.info("secret_store.created backend=local_dev env_file=%s", env_file)
        return store

    if backend == "aliyun_kms":
        msg = (
            "secret_store backend 'aliyun_kms': AliyunKmsSecretStore is "
            "implemented, but the factory needs a concrete KmsBackend — "
            "the real Aliyun SDK client is a deploy-time follow-up "
            "(STREAM-F-DESIGN Mini-ADR F-7). Use 'local_dev' for M0 "
            "dev / test, or construct AliyunKmsSecretStore(backend) "
            "directly."
        )
        raise NotImplementedError(msg)

    msg = f"unknown secret_store backend: {backend!r}"
    raise ValueError(msg)
