"""Tenant-scoped authorization and development identity adapter."""

from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.database import set_tenant_context
from enterprise_ai_api.models import Membership, Tenant, User
from enterprise_ai_core.security import (
    AccessContext,
    Action,
    PermissionDenied,
    ResourceScope,
    Role,
    authorize,
)

DevUserId = Annotated[
    UUID,
    Header(
        alias="X-Dev-User-Id",
        description="Temporary local-development identity. Replace with verified SSO claims.",
    ),
]


def ensure_development_user(session: Session, user_id: UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id, external_subject=f"dev:{user_id}")
        session.add(user)
    return user


def authorize_tenant_action(
    session: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    action: Action,
) -> Tenant:
    """Resolve tenant and membership without revealing cross-tenant existence."""

    set_tenant_context(session, tenant_id)
    tenant = session.get(Tenant, tenant_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user_id,
        )
    )
    if tenant is None or membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )

    context = AccessContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=frozenset({Role(membership.role)}),
    )
    try:
        authorize(context, ResourceScope(tenant_id=tenant_id), action)
    except PermissionDenied as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource not found or access denied",
        ) from error
    return tenant
