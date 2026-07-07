"""Phase 0.2 smoke test — verifies workspace install + namespace package wiring."""

from expert_work.common import __version__


def test_expert_work_common_version() -> None:
    """expert-work-common is importable and exposes __version__."""
    assert __version__ == "0.0.0"
