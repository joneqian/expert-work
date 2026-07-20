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
