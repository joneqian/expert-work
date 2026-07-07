"""P1-S2.1d-2 — ``EvalWorker`` lifespan wiring.

The drain worker is gated by ``enable_eval_worker`` (OFF by default). When
armed it lands on ``app.state.eval_worker`` and runs; the lifespan stops it
on shutdown. With the flag off, nothing is wired — the enqueue API still
works (it only writes ``queued`` rows), there is just no resident drainer.

Driven through ``app.router.lifespan_context`` (the same boot path the ASGI
server uses), mirroring ``test_checkpointer_wiring``. The default interval
(300s) means the loop sleeps first, so no ``tools/eval`` harness is touched.
"""

from __future__ import annotations

import pytest

from control_plane.app import create_app
from control_plane.eval_worker import EvalWorker
from control_plane.settings import Settings
from expert_work.persistence import InMemoryEvalRunStore
from tests.auth_fixtures import build_test_jwt_verifier


def _make_app(*, enable_eval_worker: bool):
    # No injected runtime — drive the real lifespan build path (where the
    # worker wiring lives), mirroring ``test_checkpointer_wiring``.
    settings = Settings(checkpointer_backend="memory", enable_eval_worker=enable_eval_worker)
    return create_app(
        settings=settings,
        jwt_verifier=build_test_jwt_verifier(),
        enable_reaper=False,
        enable_scheduler=False,
        eval_run_repo=InMemoryEvalRunStore(),
    )


@pytest.mark.asyncio
async def test_eval_worker_armed_when_enabled() -> None:
    app = _make_app(enable_eval_worker=True)
    async with app.router.lifespan_context(app):
        worker = app.state.eval_worker
        assert isinstance(worker, EvalWorker)
        assert worker.is_running
    # Lifespan exit stops the loop.
    assert not app.state.eval_worker.is_running


@pytest.mark.asyncio
async def test_eval_worker_absent_when_disabled() -> None:
    app = _make_app(enable_eval_worker=False)
    async with app.router.lifespan_context(app):
        assert getattr(app.state, "eval_worker", None) is None
