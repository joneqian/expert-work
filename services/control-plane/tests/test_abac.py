"""Unit tests for the ABAC evaluator — Stream 8.5."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from control_plane.auth.abac import ResourceAttrs, authorize_resource, conditions_match
from expert_work.protocol import BindingConditions, Role, RoleBinding

_SUBJECT = uuid4()
_TENANT = uuid4()


def _binding(conditions: BindingConditions | None, *, role: Role = Role.OPERATOR) -> RoleBinding:
    return RoleBinding(
        subject_type="user",
        subject_id=_SUBJECT,
        tenant_id=_TENANT,
        role=role,
        conditions=conditions,
        granted_by="admin",
        granted_at=datetime.now(UTC),
    )


# --- conditions_match --------------------------------------------------------


def test_none_conditions_match_any() -> None:
    assert conditions_match(None, attrs=ResourceAttrs(resource_id="x"), subject_id=_SUBJECT)


def test_empty_conditions_match_any() -> None:
    assert conditions_match(
        BindingConditions(), attrs=ResourceAttrs(resource_id="x"), subject_id=_SUBJECT
    )


def test_resource_ids_allowlist_hit_and_miss() -> None:
    c = BindingConditions(resource_ids=("agent-foo", "agent-bar"))
    assert conditions_match(c, attrs=ResourceAttrs(resource_id="agent-foo"), subject_id=_SUBJECT)
    assert not conditions_match(
        c, attrs=ResourceAttrs(resource_id="agent-zzz"), subject_id=_SUBJECT
    )
    # Unknown id fails closed against a non-empty allowlist.
    assert not conditions_match(c, attrs=ResourceAttrs(resource_id=None), subject_id=_SUBJECT)


def test_labels_superset_match() -> None:
    c = BindingConditions(labels={"team": "支持"})
    assert conditions_match(
        c, attrs=ResourceAttrs(labels={"team": "支持", "env": "dev"}), subject_id=_SUBJECT
    )
    assert not conditions_match(
        c, attrs=ResourceAttrs(labels={"team": "运维"}), subject_id=_SUBJECT
    )
    assert not conditions_match(c, attrs=ResourceAttrs(labels={}), subject_id=_SUBJECT)


def test_owner_only_matches_subject() -> None:
    c = BindingConditions(owner_only=True)
    assert conditions_match(c, attrs=ResourceAttrs(owner_id=str(_SUBJECT)), subject_id=_SUBJECT)
    assert not conditions_match(c, attrs=ResourceAttrs(owner_id=str(uuid4())), subject_id=_SUBJECT)
    # Unknown owner fails closed.
    assert not conditions_match(c, attrs=ResourceAttrs(owner_id=None), subject_id=_SUBJECT)


def test_predicates_combine_with_and() -> None:
    c = BindingConditions(resource_ids=("agent-foo",), labels={"team": "支持"}, owner_only=True)
    ok = ResourceAttrs(resource_id="agent-foo", labels={"team": "支持"}, owner_id=str(_SUBJECT))
    assert conditions_match(c, attrs=ok, subject_id=_SUBJECT)
    # any single predicate failing fails the whole.
    bad_label = ResourceAttrs(resource_id="agent-foo", labels={"team": "x"}, owner_id=str(_SUBJECT))
    assert not conditions_match(c, attrs=bad_label, subject_id=_SUBJECT)


# --- authorize_resource (additive / most-permissive) -------------------------


def test_conditioned_binding_grants_matching_instance() -> None:
    b = _binding(BindingConditions(resource_ids=("agent-foo",)))
    attrs = ResourceAttrs(resource_id="agent-foo")
    assert authorize_resource(
        resource="manifest", action="write", attrs=attrs, conditioned_bindings=[b]
    )


def test_conditioned_binding_denies_nonmatching_instance() -> None:
    b = _binding(BindingConditions(resource_ids=("agent-foo",)))
    attrs = ResourceAttrs(resource_id="agent-bar")
    assert not authorize_resource(
        resource="manifest", action="write", attrs=attrs, conditioned_bindings=[b]
    )


def test_role_without_action_grant_is_skipped() -> None:
    # VIEWER cannot write manifest even on a matching instance.
    b = _binding(BindingConditions(resource_ids=("agent-foo",)), role=Role.VIEWER)
    attrs = ResourceAttrs(resource_id="agent-foo")
    assert not authorize_resource(
        resource="manifest", action="write", attrs=attrs, conditioned_bindings=[b]
    )
    # …but VIEWER can read it.
    assert authorize_resource(
        resource="manifest", action="read", attrs=attrs, conditioned_bindings=[b]
    )


def test_unconditioned_binding_ignored_here() -> None:
    # An unconditioned binding is an RBAC fast-path grant; this function only
    # considers conditioned ones, so it returns False (and the caller's
    # is_allowed already handled the unconditioned grant).
    b = _binding(None)
    attrs = ResourceAttrs(resource_id="agent-foo")
    assert not authorize_resource(
        resource="manifest", action="write", attrs=attrs, conditioned_bindings=[b]
    )


def test_platform_scope_binding_rejects_conditions() -> None:
    import pytest

    with pytest.raises(ValueError, match="must not carry conditions"):
        RoleBinding(
            subject_type="user",
            subject_id=_SUBJECT,
            tenant_id=None,
            role=Role.SYSTEM_ADMIN,
            platform_scope=True,
            conditions=BindingConditions(owner_only=True),
            granted_by="admin",
            granted_at=datetime.now(UTC),
        )
