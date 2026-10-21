from uuid import UUID

import pytest

from enterprise_ai_core.security import (
    AccessContext,
    Action,
    PermissionDenied,
    ResourceScope,
    Role,
    TenantBoundaryViolation,
    authorize,
)

TENANT_A = UUID("00000000-0000-0000-0000-00000000000a")
TENANT_B = UUID("00000000-0000-0000-0000-00000000000b")
USER = UUID("00000000-0000-0000-0000-000000000001")


def context(*roles: Role, tenant_id: UUID = TENANT_A) -> AccessContext:
    return AccessContext(tenant_id=tenant_id, user_id=USER, roles=frozenset(roles))


def test_enterprise_admin_cannot_cross_tenant_boundary() -> None:
    with pytest.raises(TenantBoundaryViolation, match="Resource not found or access denied"):
        authorize(
            context(Role.ENTERPRISE_ADMIN),
            ResourceScope(tenant_id=TENANT_B),
            Action.READ_KNOWLEDGE,
        )


def test_cross_tenant_failure_does_not_disclose_tenant_identifiers() -> None:
    with pytest.raises(TenantBoundaryViolation) as error:
        authorize(
            context(Role.MEMBER),
            ResourceScope(tenant_id=TENANT_B),
            Action.READ_KNOWLEDGE,
        )

    assert str(TENANT_A) not in str(error.value)
    assert str(TENANT_B) not in str(error.value)


def test_member_cannot_manage_documents() -> None:
    with pytest.raises(PermissionDenied):
        authorize(
            context(Role.MEMBER),
            ResourceScope(tenant_id=TENANT_A),
            Action.MANAGE_DOCUMENTS,
        )


def test_knowledge_admin_can_manage_documents_in_own_tenant() -> None:
    authorize(
        context(Role.KNOWLEDGE_ADMIN),
        ResourceScope(tenant_id=TENANT_A),
        Action.MANAGE_DOCUMENTS,
    )


def test_knowledge_admin_cannot_view_tenant_wide_audit() -> None:
    with pytest.raises(PermissionDenied):
        authorize(
            context(Role.KNOWLEDGE_ADMIN),
            ResourceScope(tenant_id=TENANT_A),
            Action.VIEW_AUDIT,
        )


def test_multiple_roles_combine_permissions_without_crossing_tenant() -> None:
    actor = context(Role.MEMBER, Role.APPROVER)
    resource = ResourceScope(tenant_id=TENANT_A)

    authorize(actor, resource, Action.READ_KNOWLEDGE)
    authorize(actor, resource, Action.REVIEW_DECISION_PACK)


def test_empty_role_set_fails_closed() -> None:
    with pytest.raises(PermissionDenied):
        authorize(context(), ResourceScope(tenant_id=TENANT_A), Action.READ_KNOWLEDGE)

