"""Application secret storage — Stream F.6, ADR-0007.

Backend-agnostic secret access: code depends on the :class:`SecretStore`
Protocol; the concrete backend (dev ``.env`` / Aliyun KMS / future
Vault) is chosen by :func:`make_secret_store`.
"""

from expert_work.runtime.secret_store.aliyun_kms import (
    AliyunKmsSecretStore as AliyunKmsSecretStore,
)
from expert_work.runtime.secret_store.aliyun_kms import (
    FetchedSecret as FetchedSecret,
)
from expert_work.runtime.secret_store.aliyun_kms import (
    KmsBackend as KmsBackend,
)
from expert_work.runtime.secret_store.base import (
    SecretNotFoundError as SecretNotFoundError,
)
from expert_work.runtime.secret_store.base import (
    SecretStore as SecretStore,
)
from expert_work.runtime.secret_store.base import (
    SecretStoreError as SecretStoreError,
)
from expert_work.runtime.secret_store.factory import (
    SecretStoreBackend as SecretStoreBackend,
)
from expert_work.runtime.secret_store.factory import (
    make_secret_store as make_secret_store,
)
from expert_work.runtime.secret_store.local_dev import (
    LocalDevSecretStore as LocalDevSecretStore,
)
from expert_work.runtime.secret_store.refs import (
    SECRET_SCHEME as SECRET_SCHEME,
)
from expert_work.runtime.secret_store.refs import (
    InvalidSecretRefError as InvalidSecretRefError,
)
from expert_work.runtime.secret_store.refs import (
    is_secret_ref as is_secret_ref,
)
from expert_work.runtime.secret_store.refs import (
    parse_secret_ref as parse_secret_ref,
)

__all__ = [
    "SECRET_SCHEME",
    "AliyunKmsSecretStore",
    "FetchedSecret",
    "InvalidSecretRefError",
    "KmsBackend",
    "LocalDevSecretStore",
    "SecretNotFoundError",
    "SecretStore",
    "SecretStoreBackend",
    "SecretStoreError",
    "is_secret_ref",
    "make_secret_store",
    "parse_secret_ref",
]
