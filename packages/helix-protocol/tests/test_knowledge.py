"""Schema tests for the J.5 knowledge DTOs."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from helix_agent.protocol import (
    DocumentStatus,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
)


def test_document_status_values() -> None:
    assert {s.value for s in DocumentStatus} == {"pending", "processing", "ready", "failed"}


def test_knowledge_base_constructs() -> None:
    kb = KnowledgeBase(id=uuid4(), tenant_id=uuid4(), name="hr-policies")
    assert kb.name == "hr-policies"
    assert kb.created_at is None


def test_knowledge_document_constructs() -> None:
    doc = KnowledgeDocument(
        id=uuid4(),
        tenant_id=uuid4(),
        kb_id=uuid4(),
        filename="handbook.pdf",
        status=DocumentStatus.READY,
        chunk_count=12,
    )
    assert doc.status is DocumentStatus.READY
    assert doc.chunk_count == 12
    assert doc.error is None


def test_knowledge_document_rejects_negative_chunk_count() -> None:
    with pytest.raises(ValidationError):
        KnowledgeDocument(
            id=uuid4(),
            tenant_id=uuid4(),
            kb_id=uuid4(),
            filename="x.pdf",
            status=DocumentStatus.PENDING,
            chunk_count=-1,
        )


def test_knowledge_chunk_constructs() -> None:
    chunk = KnowledgeChunk(
        id=uuid4(),
        tenant_id=uuid4(),
        kb_id=uuid4(),
        document_id=uuid4(),
        chunk_index=0,
        content="some text",
        embedding=(0.1, 0.2, 0.3),
    )
    assert chunk.chunk_index == 0
    assert chunk.embedding == (0.1, 0.2, 0.3)


def test_knowledge_chunk_rejects_negative_index() -> None:
    with pytest.raises(ValidationError):
        KnowledgeChunk(
            id=uuid4(),
            tenant_id=uuid4(),
            kb_id=uuid4(),
            document_id=uuid4(),
            chunk_index=-1,
            content="x",
            embedding=(0.0,),
        )


def test_knowledge_dtos_are_frozen() -> None:
    kb = KnowledgeBase(id=uuid4(), tenant_id=uuid4(), name="kb")
    with pytest.raises(ValidationError):
        kb.name = "other"  # type: ignore[misc]
