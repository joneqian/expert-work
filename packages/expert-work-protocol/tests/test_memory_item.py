def test_memory_item_access_count_defaults_zero() -> None:
    from uuid import uuid4

    from expert_work.protocol import MemoryItem

    item = MemoryItem(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind="fact",
        content="x",
        embedding=(0.1,),
    )
    assert item.access_count == 0


def test_memory_item_bitemporal_fields_default_none() -> None:
    from uuid import uuid4

    from expert_work.protocol import MemoryItem

    item = MemoryItem(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind="fact",
        content="user lives in Shanghai",
        embedding=(0.1, 0.2, 0.3),
    )
    assert item.source_run_id is None
    assert item.valid_at is None
    assert item.expired_at is None
    assert item.invalid_at is None
    assert item.supersedes is None
    assert item.superseded_by is None
    assert item.expected_valid_days is None


def test_memory_item_bitemporal_fields_roundtrip() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from expert_work.protocol import MemoryItem

    old_id = uuid4()
    run_id = uuid4()
    now = datetime(2026, 7, 21, tzinfo=UTC)
    item = MemoryItem(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        kind="fact",
        content="user lives in Beijing",
        embedding=(0.1, 0.2, 0.3),
        source_run_id=str(run_id),
        valid_at=now,
        supersedes=old_id,
        expected_valid_days=90,
    )
    assert item.source_run_id == str(run_id)
    assert item.valid_at == now
    assert item.supersedes == old_id
    assert item.expected_valid_days == 90
