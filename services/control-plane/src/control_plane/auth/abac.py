"""Attribute-based access control — Stream 8.5 (fine-grained RBAC-ABAC).

The RBAC matrix (:mod:`control_plane.auth.rbac`) decides ``(role, resource_type,
action)`` at the **type** level. ABAC narrows a grant to specific resource
**instances** via :class:`expert_work.protocol.BindingConditions` carried on a
tenant-scope role binding.

Decision model (additive / most-permissive — see design doc §1.3):

* An **unconditioned** grant (a JWT realm role, ``system_admin``, or a binding
  with no conditions) authorises any instance — the RBAC fast path in
  :func:`control_plane.api._authz` handles it via ``is_allowed``.
* A **conditioned** binding authorises *only* the instances whose attributes
  satisfy its conditions — that is this module's job, evaluated on the slow
  path when ``is_allowed`` already returned ``False``.

Everything here is pure (no IO, no audit) so it unit-tests in isolation; the
binding lookup + audit happen at the FastAPI dependency layer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import UUID

from control_plane.auth.rbac import Action, Resource, grants_for
from expert_work.protocol import BindingConditions, RoleBinding


@dataclass(frozen=True)
class ResourceAttrs:
    """The instance attributes ABAC conditions are evaluated against.

    ``resource_id`` is the URI-level identity (an agent name, a session id …);
    ``labels`` mirrors the resource's ``metadata.labels``; ``owner_id`` is the
    subject that created it (string form, matched against the binding subject).
    """

    resource_id: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    owner_id: str | None = None


def conditions_match(
    conditions: BindingConditions | None,
    *,
    attrs: ResourceAttrs,
    subject_id: UUID,
) -> bool:
    """``True`` iff ``attrs`` satisfies every predicate in ``conditions``.

    ``None`` / all-empty conditions match any instance. Each predicate is
    AND-combined; a set predicate that cannot be evaluated (e.g. ``owner_only``
    with an unknown ``owner_id``) fails closed.
    """
    if conditions is None or conditions.is_empty:
        return True

    if conditions.resource_ids:
        if attrs.resource_id is None or attrs.resource_id not in conditions.resource_ids:
            return False

    for key, value in conditions.labels.items():
        if attrs.labels.get(key) != value:
            return False

    if conditions.owner_only:
        # Fail closed when ownership is unknown rather than granting.
        if attrs.owner_id is None or attrs.owner_id != str(subject_id):
            return False

    return True


def authorize_resource(
    *,
    resource: Resource,
    action: Action,
    attrs: ResourceAttrs,
    conditioned_bindings: Iterable[RoleBinding],
) -> bool:
    """Instance-level grant from conditioned bindings (RBAC fast path missed).

    For each conditioned binding whose role grants ``(resource, action)`` AND
    whose conditions match ``attrs``, the access is allowed. Bindings without
    conditions are ignored here — they are an unconditioned grant the caller
    already evaluated via ``is_allowed`` (and must NOT be merged into
    ``principal.roles``; see :func:`control_plane.auth.tenant_roles`).
    """
    for binding in conditioned_bindings:
        if not binding.has_conditions:
            continue
        if action not in grants_for(binding.role).get(resource, set()):
            continue
        if conditions_match(binding.conditions, attrs=attrs, subject_id=binding.subject_id):
            return True
    return False


__all__ = ["ResourceAttrs", "authorize_resource", "conditions_match"]
