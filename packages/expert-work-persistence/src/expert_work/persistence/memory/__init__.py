"""Long-term memory repository — Stream J.3.

Cross-session memory for the per-user persistent agent: ``fact`` /
``episodic`` rows with embeddings, retrieved by cosine similarity.
See ``docs/streams/STREAM-J-DESIGN.md`` § 8.
"""

from expert_work.persistence.memory.base import MemoryStore as MemoryStore
from expert_work.persistence.memory.dlq import (
    DLQRow as DLQRow,
)
from expert_work.persistence.memory.dlq import (
    InMemoryMemoryWritebackDLQ as InMemoryMemoryWritebackDLQ,
)
from expert_work.persistence.memory.dlq import (
    MemoryWritebackDLQ as MemoryWritebackDLQ,
)
from expert_work.persistence.memory.dlq import (
    SqlMemoryWritebackDLQ as SqlMemoryWritebackDLQ,
)
from expert_work.persistence.memory.hash import (
    hash_content as hash_content,
)
from expert_work.persistence.memory.hash import (
    normalise_content as normalise_content,
)
from expert_work.persistence.memory.memory import (
    InMemoryMemoryStore as InMemoryMemoryStore,
)
from expert_work.persistence.memory.sql import SqlMemoryStore as SqlMemoryStore

__all__ = [
    "DLQRow",
    "InMemoryMemoryStore",
    "InMemoryMemoryWritebackDLQ",
    "MemoryStore",
    "MemoryWritebackDLQ",
    "SqlMemoryStore",
    "SqlMemoryWritebackDLQ",
    "hash_content",
    "normalise_content",
]
