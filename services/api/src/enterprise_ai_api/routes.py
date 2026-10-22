"""Permission-aware tenant administration endpoints."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import Membership, Space, SpaceMembership, Tenant, User
from enterprise_ai_api.schemas import (
    MembershipCreate,
    MembershipResponse,
    MembershipStatusUpdate,
    SpaceCreate,
    SpaceMembershipCreate,
    SpaceMembershipResponse,
    SpaceResponse,
    TenantCreate,
    TenantResponse,
    TenantSecurityPolicyUpdate,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    accessible_space_ids,
    authorize_space_access,
    authorize_tenant_action,
    ensure_development_user,
    tenant_mfa_required,
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
    user = ensure_development_user(session, user_id)
    session.add(tenant)
    # The membership references both parent rows. Flush them first so PostgreSQL
    # never attempts the membership INSERT before its tenant and user exist.
    session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=user_id, role="enterprise_admin"))
    if user.default_tenant_id is None:
        user.default_tenant_id = tenant.id
    record_audit_event(
        session,
        tenant_id=tenant.id,
        actor_id=user_id,
        action="tenant.created",
        resource_type="tenant",
        resource_id=tenant.id,
    )
    session.commit()
    set_tenant_context(session, tenant.id)
    session.refresh(tenant)
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> TenantResponse:
    tenant = authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
        enforce_mfa=False,
    )
    membership = session.get(Membership, (tenant_id, user_id))
    user = session.get(User, user_id)
    required = (
        membership is not None
        and tenant_mfa_required(tenant, membership)
        and (user is None or user.mfa_enabled_at is None)
    )
    return TenantResponse.model_validate(tenant).model_copy(
        update={"mfa_required_for_current_user": required}
    )


@router.patch("/tenants/{tenant_id}/security-policy", response_model=TenantResponse)
def update_tenant_security_policy(
    tenant_id: UUID,
    payload: TenantSecurityPolicyUpdate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> TenantResponse:
    tenant = authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_MEMBERS,
        enforce_mfa=False,
    )
    user = session.get(User, user_id)
    membership = session.get(Membership, (tenant_id, user_id))
    if (
        membership is not None
        and tenant_mfa_required(tenant, membership)
        and (user is None or user.mfa_enabled_at is None)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA required by enterprise policy",
        )
    if payload.mfa_policy != "off" and (user is None or user.mfa_enabled_at is None):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Enable MFA before enforcing an enterprise MFA policy",
        )
    previous = tenant.mfa_policy
    tenant.mfa_policy = payload.mfa_policy
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="tenant.security_policy_updated",
        resource_type="tenant",
        resource_id=tenant_id,
        details={"previous_mfa_policy": previous, "mfa_policy": payload.mfa_policy},
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(tenant)
    return TenantResponse.model_validate(tenant)


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
        if not membership.active:
            raise HTTPException(
                status_code=409, detail="Restore suspended membership explicitly first"
            )
        membership.role = payload.role
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="membership.saved",
        resource_type="membership",
        resource_id=payload.user_id,
        details={"role": payload.role},
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(membership)
    return membership


@router.patch("/tenants/{tenant_id}/members/{target_user_id}/status",
              response_model=MembershipResponse)
def update_member_status(
    tenant_id: UUID, target_user_id: UUID, payload: MembershipStatusUpdate,
    user_id: CurrentUserId, session: DatabaseSession,
) -> Membership:
    authorize_tenant_action(session, tenant_id=tenant_id, user_id=user_id,
                           action=Action.MANAGE_MEMBERS)
    if target_user_id == user_id and not payload.active:
        raise HTTPException(status_code=409, detail="Administrators cannot suspend themselves")
    # Serialize changes for a tenant on PostgreSQL and SQLite. Recheck the actor after
    # the lock: two admins must not concurrently suspend one another using stale access.
    session.execute(update(Tenant).where(Tenant.id == tenant_id).values(status=Tenant.status))
    session.expire_all()
    authorize_tenant_action(session, tenant_id=tenant_id, user_id=user_id,
                           action=Action.MANAGE_MEMBERS)
    membership = session.get(Membership, (tenant_id, target_user_id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    if membership.active != payload.active:
        membership.active = payload.active
        record_audit_event(
            session, tenant_id=tenant_id, actor_id=user_id,
            action="membership.restored" if payload.active else "membership.suspended",
            resource_type="membership", resource_id=target_user_id,
            details={"active": payload.active},
        )
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
        session.flush()
        session.add(
            SpaceMembership(
                tenant_id=tenant_id,
                space_id=space.id,
                user_id=user_id,
                role="manager",
            )
        )
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="space.created",
            resource_type="space",
            resource_id=space.id,
            details={"classification": payload.classification},
        )
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
    allowed_ids = accessible_space_ids(
        session, tenant_id=tenant_id, user_id=user_id
    )
    if not allowed_ids:
        return []
    return list(
        session.scalars(
            select(Space)
            .where(Space.tenant_id == tenant_id, Space.id.in_(allowed_ids))
            .order_by(Space.created_at, Space.id)
        )
    )


@router.get(
    "/tenants/{tenant_id}/spaces/{space_id}/members",
    response_model=list[SpaceMembershipResponse],
)
def list_space_members(
    tenant_id: UUID,
    space_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> list[SpaceMembership]:
    authorize_tenant_action(
        session, tenant_id=tenant_id, user_id=user_id, action=Action.READ_KNOWLEDGE
    )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
        manage=True,
    )
    return list(
        session.scalars(
            select(SpaceMembership)
            .where(
                SpaceMembership.tenant_id == tenant_id,
                SpaceMembership.space_id == space_id,
            )
            .order_by(SpaceMembership.created_at, SpaceMembership.user_id)
        )
    )


@router.post(
    "/tenants/{tenant_id}/spaces/{space_id}/members",
    response_model=SpaceMembershipResponse,
)
def save_space_member(
    tenant_id: UUID,
    space_id: UUID,
    payload: SpaceMembershipCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> SpaceMembership:
    authorize_tenant_action(
        session, tenant_id=tenant_id, user_id=user_id, action=Action.READ_KNOWLEDGE
    )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
        manage=True,
    )
    target_membership = session.get(Membership, (tenant_id, payload.user_id))
    if target_membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    grant = session.get(SpaceMembership, (tenant_id, space_id, payload.user_id))
    if grant is None:
        grant = SpaceMembership(
            tenant_id=tenant_id,
            space_id=space_id,
            user_id=payload.user_id,
            role=payload.role,
        )
        session.add(grant)
    else:
        grant.role = payload.role
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="space_access.saved",
        resource_type="space",
        resource_id=space_id,
        details={"target_user_id": str(payload.user_id), "role": payload.role},
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(grant)
    return grant


@router.delete(
    "/tenants/{tenant_id}/spaces/{space_id}/members/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_space_member(
    tenant_id: UUID,
    space_id: UUID,
    target_user_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> None:
    authorize_tenant_action(
        session, tenant_id=tenant_id, user_id=user_id, action=Action.READ_KNOWLEDGE
    )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
        manage=True,
    )
    grant = session.get(SpaceMembership, (tenant_id, space_id, target_user_id))
    if grant is not None:
        if target_user_id == user_id:
            managers = list(
                session.scalars(
                    select(SpaceMembership).where(
                        SpaceMembership.tenant_id == tenant_id,
                        SpaceMembership.space_id == space_id,
                        SpaceMembership.role == "manager",
                    )
                )
            )
            if len(managers) <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The last space manager cannot remove their own access",
                )
        session.delete(grant)
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="space_access.removed",
            resource_type="space",
            resource_id=space_id,
            details={"target_user_id": str(target_user_id), "previous_role": grant.role},
        )
        session.commit()
