"""Keycloak Admin integration — Stream R (member account provisioning).

helix is the orchestrator; Keycloak is the single IdP. This package provisions
member accounts (``create_user`` + native set-password email) so a tenant admin
can invite employees without anyone touching the Keycloak console. See
``docs/streams/STREAM-R-DESIGN.md`` § 4.
"""

from control_plane.keycloak.admin_client import (
    HttpKeycloakAdminClient as HttpKeycloakAdminClient,
)
from control_plane.keycloak.admin_client import (
    KeycloakAdminClient as KeycloakAdminClient,
)
from control_plane.keycloak.admin_client import KeycloakUser as KeycloakUser
from control_plane.keycloak.errors import KeycloakAuthError as KeycloakAuthError
from control_plane.keycloak.errors import KeycloakError as KeycloakError
from control_plane.keycloak.errors import (
    KeycloakUnavailableError as KeycloakUnavailableError,
)
from control_plane.keycloak.errors import (
    KeycloakUserExistsError as KeycloakUserExistsError,
)
from control_plane.keycloak.fake_admin_client import (
    FakeKeycloakAdminClient as FakeKeycloakAdminClient,
)
from control_plane.keycloak.token import (
    ServiceAccountTokenProvider as ServiceAccountTokenProvider,
)

__all__ = [
    "FakeKeycloakAdminClient",
    "HttpKeycloakAdminClient",
    "KeycloakAdminClient",
    "KeycloakAuthError",
    "KeycloakError",
    "KeycloakUnavailableError",
    "KeycloakUser",
    "KeycloakUserExistsError",
    "ServiceAccountTokenProvider",
]
