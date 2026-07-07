"""Persistence backends for the Stream C.3 auth tables.

Public surface (one Protocol + an in-memory + a SQL impl per store):

* ``service_account``  — :class:`ServiceAccountStore`
* ``api_key``          — :class:`ApiKeyStore`
* ``role_binding``     — :class:`RoleBindingStore`

Plus duplicate-row sentinel exceptions used by the API layer to map a
unique-constraint violation onto an HTTP 409.
"""

from expert_work.persistence.auth.base import (
    ApiKeyStore,
    DuplicateApiKeyPrefixError,
    DuplicateRoleBindingError,
    DuplicateServiceAccountError,
    RoleBindingStore,
    ServiceAccountStore,
)
from expert_work.persistence.auth.memory import (
    InMemoryApiKeyStore,
    InMemoryRoleBindingStore,
    InMemoryServiceAccountStore,
)
from expert_work.persistence.auth.sql import (
    SqlApiKeyStore,
    SqlRoleBindingStore,
    SqlServiceAccountStore,
)

__all__ = [
    "ApiKeyStore",
    "DuplicateApiKeyPrefixError",
    "DuplicateRoleBindingError",
    "DuplicateServiceAccountError",
    "InMemoryApiKeyStore",
    "InMemoryRoleBindingStore",
    "InMemoryServiceAccountStore",
    "RoleBindingStore",
    "ServiceAccountStore",
    "SqlApiKeyStore",
    "SqlRoleBindingStore",
    "SqlServiceAccountStore",
]
