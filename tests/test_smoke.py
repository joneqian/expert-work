"""Phase 0.2 smoke test — verifies workspace install + namespace package wiring."""

from helix_agent.common import __version__


def test_helix_agent_common_version() -> None:
    """helix-agent-common is importable and exposes __version__."""
    assert __version__ == "0.0.0"
