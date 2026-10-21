"""Permission-aware tenant administration endpoints."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import Membership, Space, Tenant
from enterprise_ai_api.schemas import (
    MembershipCreate,
    MembershipResponse,
    SpaceCreate,
    SpaceResponse,
    TenantCreate,
    TenantResponse,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    authorize_tenant_action,
    ensure_development_user,
)
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> Tenant:
    tenant = Tenant(id=uuid4(), name=payload.name)
    set_tenant_context(session, tenant.id)
    ensure_development_user(session, user_id)
    session.add(tenant)
    session.add(Membership(tenant_id=tenant.id, user_id=user_id, role="enterprise_admin"))
    session.commit()
    set_tenant_context(session, tenant.id)
    session.refresh(tenant)
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant(tenant_id: UUID, user_id: CurrentUserId, session: DatabaseSession) -> Tenant:
    return authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )


@router.post(
    "/tenants/{tenant_id}/members",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    tenant_id: UUID,
    payload: MembershipCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> Membership:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_MEMBERS,
    )
    ensure_development_user(session, payload.user_id)
    membership = session.get(Membership, (tenant_id, payload.user_id))
    if membership is None:
        membership = Membership(tenant_id=tenant_id, user_id=payload.user_id, role=payload.role)
        session.add(membership)
    else:
        membership.role = payload.role
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(membership)
    return membership


@router.get("/tenants/{tenant_id}/members", response_model=list[MembershipResponse])
def list_members(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> list[Membership]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_MEMBERS,
    )
    return list(
        session.scalars(
            select(Membership)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.created_at, Membership.user_id)
        )
    )


@router.post(
    "/tenants/{tenant_id}/spaces",
    response_model=SpaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_space(
    tenant_id: UUID,
    payload: SpaceCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> Space:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_SPACES,
    )
    space = Space(
        tenant_id=tenant_id,
        name=payload.name,
        classification=payload.classification,
    )
    session.add(space)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A space with this name already exists in the tenant",
        ) from error
    set_tenant_context(session, tenant_id)
    session.refresh(space)
    return space


@router.get("/tenants/{tenant_id}/spaces", response_model=list[SpaceResponse])
def list_spaces(tenant_id: UUID, user_id: CurrentUserId, session: DatabaseSession) -> list[Space]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    return list(
        session.scalars(
            select(Space).where(Space.tenant_id == tenant_id).order_by(Space.created_at, Space.id)
        )
    )
