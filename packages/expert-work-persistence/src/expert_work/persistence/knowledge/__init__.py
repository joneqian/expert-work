"""Knowledge base / RAG repository — Stream J.5.

Tenant-scoped knowledge bases of uploaded, chunked, embedded documents,
retrieved by cosine similarity. See ``docs/streams/STREAM-J-DESIGN.md`` § 12.
"""

from expert_work.persistence.knowledge.base import UNSET as UNSET
from expert_work.persistence.knowledge.base import (
    DuplicateKnowledgeBaseError as DuplicateKnowledgeBaseError,
)
from expert_work.persistence.knowledge.base import KnowledgeStore as KnowledgeStore
from expert_work.persistence.knowledge.memory import (
    InMemoryKnowledgeStore as InMemoryKnowledgeStore,
)
from expert_work.persistence.knowledge.sql import SqlKnowledgeStore as SqlKnowledgeStore

__all__ = [
    "UNSET",
    "DuplicateKnowledgeBaseError",
    "InMemoryKnowledgeStore",
    "KnowledgeStore",
    "SqlKnowledgeStore",
]
