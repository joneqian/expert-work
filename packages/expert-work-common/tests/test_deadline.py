"""Unit tests for :mod:`expert_work.common.deadline`."""

from __future__ import annotations

import asyncio

import pytest

from expert_work.common.deadline import (
    CancelledByUserError,
    CancelToken,
    DeadlineContext,
    DeadlineExceededError,
    deadline_check,
    get_current_deadline,
    with_deadline,
)

# ---------------------------------------------------------------------------
# DeadlineContext basics
# ---------------------------------------------------------------------------


def test_root_remaining_is_close_to_budget() -> None:
    ctx = DeadlineContext.root(1_000)  # 1 s
    remaining = ctx.remaining_ms()
    # Allow a small drift for clock granularity; we just started the clock.
    assert 950 <= remaining <= 1000


def test_derived_clips_when_parent_is_tighter() -> None:
    parent = DeadlineContext.root(100, layer="request")  # 100 ms
    child = parent.derived("step", 60_000)  # asked for 60 s
    # Child cannot outlive parent — compare absolute deadlines (not
    # remaining_ms, which races the wall clock between calls).
    assert child.deadline_ms <= parent.deadline_ms
    # Child carries a reference back so observability can walk the chain.
    assert child.parent is parent
    # Cancel token is shared up the chain.
    assert child.cancel_token is parent.cancel_token


def test_derived_uses_smaller_of_parent_and_request() -> None:
    parent = DeadlineContext.root(60_000)  # 60 s
    child = parent.derived("tool", 1_000)  # 1 s
    assert child.remaining_ms() <= 1_000


def test_unknown_layer_rejected_at_root() -> None:
    with pytest.raises(ValueError, match="unknown deadline layer"):
        DeadlineContext.root(1_000, layer="ufo")


def test_unknown_layer_rejected_at_derive() -> None:
    parent = DeadlineContext.root(1_000)
    with pytest.raises(ValueError, match="unknown deadline layer"):
        parent.derived("ufo", 100)


def test_from_absolute_keeps_supplied_token() -> None:
    token = CancelToken()
    deadline_ms = 9999999999999.0  # far future
    ctx = DeadlineContext.from_absolute(deadline_ms, layer="request", cancel_token=token)
    assert ctx.cancel_token is token
    assert ctx.deadline_ms == deadline_ms


# ---------------------------------------------------------------------------
# with_deadline contextmanager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_deadline_root_sets_contextvar() -> None:
    assert get_current_deadline() is None
    async with with_deadline("request", 1_000) as ctx:
        assert get_current_deadline() is ctx
    # Restored on exit.
    assert get_current_deadline() is None


@pytest.mark.asyncio
async def test_with_deadline_nested_derives_from_parent() -> None:
    async with with_deadline("request", 60_000) as outer:
        async with with_deadline("step", 5_000) as inner:
            assert inner.parent is outer
            assert inner.cancel_token is outer.cancel_token


@pytest.mark.asyncio
async def test_with_deadline_clips_nested_child() -> None:
    async with with_deadline("request", 100):  # 100 ms outer
        async with with_deadline("step", 60_000) as inner:
            assert inner.remaining_ms() <= 100


@pytest.mark.asyncio
async def test_with_deadline_inner_cancel_token_ignored_when_parent_exists() -> None:
    """Inside a nested scope the parent's token always wins so the chain
    has a single source of cancellation."""
    parent_token = CancelToken()
    async with with_deadline("request", 60_000, cancel_token=parent_token) as outer:
        own_token = CancelToken()
        async with with_deadline("step", 1_000, cancel_token=own_token) as inner:
            assert inner.cancel_token is outer.cancel_token
            assert inner.cancel_token is parent_token


# ---------------------------------------------------------------------------
# deadline_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deadline_check_noop_outside_scope() -> None:
    # No active context — must not raise.
    await deadline_check()


@pytest.mark.asyncio
async def test_deadline_check_raises_when_expired() -> None:
    async with with_deadline("request", 1):  # 1 ms — already expired
        await asyncio.sleep(0.01)
        with pytest.raises(DeadlineExceededError) as exc:
            await deadline_check()
        assert exc.value.layer == "request"


@pytest.mark.asyncio
async def test_deadline_check_reports_innermost_layer() -> None:
    async with with_deadline("request", 1):  # outer expired
        async with with_deadline("step", 1):
            await asyncio.sleep(0.01)
            with pytest.raises(DeadlineExceededError) as exc:
                await deadline_check()
            # Innermost layer surfaces first.
            assert exc.value.layer == "step"


# ---------------------------------------------------------------------------
# CancelToken
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_token_starts_uncancelled() -> None:
    token = CancelToken()
    assert token.cancelled is False


@pytest.mark.asyncio
async def test_cancel_token_cancel_is_idempotent() -> None:
    token = CancelToken()
    token.cancel()
    token.cancel()
    assert token.cancelled is True


@pytest.mark.asyncio
async def test_deadline_check_raises_on_cancel_token() -> None:
    async with with_deadline("request", 60_000) as ctx:
        ctx.cancel_token.cancel()
        with pytest.raises(CancelledByUserError):
            await deadline_check()


@pytest.mark.asyncio
async def test_cancel_token_wait_unblocks_on_cancel() -> None:
    token = CancelToken()

    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.01)
        token.cancel()

    asyncio.create_task(_cancel_after_delay())  # noqa: RUF006
    await asyncio.wait_for(token.wait(), timeout=1.0)
    assert token.cancelled is True


@pytest.mark.asyncio
async def test_cancel_token_propagates_to_nested_scopes() -> None:
    """Cancelling the outer token must fire ``deadline_check`` in every
    nested scope, since the chain shares one token."""
    async with with_deadline("request", 60_000) as outer:
        outer.cancel_token.cancel()
        async with with_deadline("step", 1_000):
            with pytest.raises(CancelledByUserError):
                await deadline_check()
