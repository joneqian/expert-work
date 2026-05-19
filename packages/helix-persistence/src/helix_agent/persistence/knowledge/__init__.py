"""Knowledge base / RAG repository — Stream J.5.

Tenant-scoped knowledge bases of uploaded, chunked, embedded documents,
retrieved by cosine similarity. See ``docs/streams/STREAM-J-DESIGN.md`` § 12.
"""

from helix_agent.persistence.knowledge.base import (
    DuplicateKnowledgeBaseError as DuplicateKnowledgeBaseError,
)
from helix_agent.persistence.knowledge.base import KnowledgeStore as KnowledgeStore
from helix_agent.persistence.knowledge.memory import (
    InMemoryKnowledgeStore as InMemoryKnowledgeStore,
)
from helix_agent.persistence.knowledge.sql import SqlKnowledgeStore as SqlKnowledgeStore

__all__ = [
    "DuplicateKnowledgeBaseError",
    "InMemoryKnowledgeStore",
    "KnowledgeStore",
    "SqlKnowledgeStore",
]
