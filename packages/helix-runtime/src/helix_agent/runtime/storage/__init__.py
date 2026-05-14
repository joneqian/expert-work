"""Object storage abstraction (per ADR-0004).

S3-compatible Protocol with in-memory + aiobotocore implementations. The
factory ``make_object_store`` mirrors the checkpointer / store / stream
bridge pattern for consistent lifespan management.
"""

from helix_agent.runtime.storage.base import LockMode as LockMode
from helix_agent.runtime.storage.base import ObjectLockedError as ObjectLockedError
from helix_agent.runtime.storage.base import ObjectNotFoundError as ObjectNotFoundError
from helix_agent.runtime.storage.base import ObjectStore as ObjectStore
from helix_agent.runtime.storage.base import ObjectStoreError as ObjectStoreError
from helix_agent.runtime.storage.factory import (
    ObjectStoreBackend as ObjectStoreBackend,
)
from helix_agent.runtime.storage.factory import (
    S3CompatibleConfig as S3CompatibleConfig,
)
from helix_agent.runtime.storage.factory import (
    make_object_store as make_object_store,
)
from helix_agent.runtime.storage.memory import (
    InMemoryObjectStore as InMemoryObjectStore,
)
from helix_agent.runtime.storage.s3_compatible import (
    S3CompatibleObjectStore as S3CompatibleObjectStore,
)

__all__ = [
    "InMemoryObjectStore",
    "LockMode",
    "ObjectLockedError",
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreBackend",
    "ObjectStoreError",
    "S3CompatibleConfig",
    "S3CompatibleObjectStore",
    "make_object_store",
]
