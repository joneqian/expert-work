"""FastAPI routers for the Control Plane (Streams B + C)."""

from control_plane.api.agents import build_agents_router
from control_plane.api.api_keys import build_api_keys_router
from control_plane.api.artifacts import build_artifacts_router
from control_plane.api.feedback import build_feedback_router
from control_plane.api.health import build_health_router
from control_plane.api.knowledge import build_knowledge_router
from control_plane.api.metrics import build_metrics_router
from control_plane.api.quota import build_quota_router
from control_plane.api.role_bindings import build_role_bindings_router
from control_plane.api.runs import build_runs_router
from control_plane.api.service_accounts import build_service_accounts_router
from control_plane.api.sessions import build_sessions_router
from control_plane.api.tenant_config import build_tenant_config_router
from control_plane.api.tenant_quotas import build_tenant_quotas_router

__all__ = [
    "build_agents_router",
    "build_api_keys_router",
    "build_artifacts_router",
    "build_feedback_router",
    "build_health_router",
    "build_knowledge_router",
    "build_metrics_router",
    "build_quota_router",
    "build_role_bindings_router",
    "build_runs_router",
    "build_service_accounts_router",
    "build_sessions_router",
    "build_tenant_config_router",
    "build_tenant_quotas_router",
]
