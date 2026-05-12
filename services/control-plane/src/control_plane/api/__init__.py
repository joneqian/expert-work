"""FastAPI routers for the Control Plane (Stream B)."""

from control_plane.api.health import build_health_router
from control_plane.api.metrics import build_metrics_router

__all__ = ["build_health_router", "build_metrics_router"]
