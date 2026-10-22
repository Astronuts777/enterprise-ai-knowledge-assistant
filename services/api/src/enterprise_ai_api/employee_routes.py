"""Tenant-local employee directory and owner-managed document grants."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.document_access import authorize_document, is_owner
from enterprise_ai_api.models import Document, DocumentGrant, Membership
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action, tenant_membership
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1/tenants/{tenant_id}")
DatabaseSession = Annotated[Session, Depends(get_session)]


class EmployeeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(max_length=100)
    department: str = Field(max_length=100)


class EmployeeResponse(EmployeeProfile):
    user_id: UUID
    role: str
    active: bool
    is_owner: bool


class AccessMode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_mode: Literal["space", "restricted"]


class GrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["reader", "editor"]


def _employee(member, owner):
    return EmployeeResponse(
        user_id=member.user_id,
        role=member.role,
        display_name=member.display_name or "",
        department=member.department or "",
        active=member.active and member.provisioning_active,
        is_owner=member.user_id == owner,
    )


@router.get("/employees", response_model=list[EmployeeResponse])
def employees(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    tenant = authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    members = session.scalars(
        select(Membership)
        .where(
            Membership.tenant_id == tenant_id,
        )
        .order_by(Membership.created_at, Membership.user_id)
        .offset(offset)
        .limit(limit)
    )
    # Directory deliberately excludes emails, authentication IDs, and session data.
    return [_employee(member, tenant.owner_user_id) for member in members]


@router.patch("/employees/{target_id}", response_model=EmployeeResponse)
def profile(
    tenant_id: UUID,
    target_id: UUID,
    payload: EmployeeProfile,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    tenant = authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE if target_id == user_id else Action.MANAGE_MEMBERS,
    )
    member = session.get(Membership, (tenant_id, target_id))
    if member is None:
        raise HTTPException(404, "Resource not found or access denied")
    if tenant.owner_user_id == target_id and target_id != user_id:
        raise HTTPException(403, "Only the owner can edit their own profile")
    member.display_name = payload.display_name.strip()
    member.department = payload.department.strip()
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="employee.profile_updated",
        resource_type="membership",
        resource_id=target_id,
    )
    result = _employee(member, tenant.owner_user_id)
    session.commit()
    return result


def _owner_document(session, tenant_id, user_id, document_id, *, lock=False):
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    if not is_owner(session, tenant_id, user_id):
        raise HTTPException(403, "Only the enterprise owner can manage document access")
    if lock:
        session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
            .values(title=Document.title)
        )
        session.expire_all()
        authorize_tenant_action(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            action=Action.READ_KNOWLEDGE,
        )
        if not is_owner(session, tenant_id, user_id):
            raise HTTPException(403, "Enterprise ownership changed")
    return authorize_document(session, tenant_id, user_id, document_id)


@router.get("/documents/{document_id}/access")
def document_access(
    tenant_id: UUID, document_id: UUID, user_id: CurrentUserId, session: DatabaseSession
):
    document = _owner_document(session, tenant_id, user_id, document_id)
    grants = session.scalars(
        select(DocumentGrant).where(
            DocumentGrant.tenant_id == tenant_id,
            DocumentGrant.document_id == document_id,
        )
    )
    return {
        "access_mode": document.access_mode,
        "grants": [{"user_id": g.user_id, "role": g.role} for g in grants],
    }


@router.put("/documents/{document_id}/access")
def access_mode(
    tenant_id: UUID,
    document_id: UUID,
    payload: AccessMode,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    document = _owner_document(session, tenant_id, user_id, document_id, lock=True)
    previous = document.access_mode
    document.access_mode = payload.access_mode
    if previous != payload.access_mode:
        # Never resurrect stale grants when a document is restricted again.
        session.execute(
            delete(DocumentGrant).where(
                DocumentGrant.tenant_id == tenant_id,
                DocumentGrant.document_id == document_id,
            )
        )
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.access_mode_changed",
        resource_type="document",
        resource_id=document_id,
        details={"previous": previous, "access_mode": payload.access_mode},
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    return {"access_mode": document.access_mode}


@router.put("/documents/{document_id}/grants/{target_id}")
def save_grant(
    tenant_id: UUID,
    document_id: UUID,
    target_id: UUID,
    payload: GrantInput,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    document = _owner_document(session, tenant_id, user_id, document_id, lock=True)
    if document.access_mode != "restricted":
        raise HTTPException(409, "Restrict this document before granting individual access")
    if target_id == user_id or tenant_membership(session, tenant_id, target_id) is None:
        raise HTTPException(409, "Choose an active non-owner employee in this enterprise")
    grant = session.get(DocumentGrant, (tenant_id, document_id, target_id))
    if grant is None:
        grant = DocumentGrant(tenant_id=tenant_id, document_id=document_id, user_id=target_id)
        session.add(grant)
    grant.role = payload.role
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.grant_saved",
        resource_type="document",
        resource_id=document_id,
        details={"user_id": str(target_id), "role": payload.role},
    )
    session.commit()
    return {"user_id": target_id, "role": payload.role}


@router.delete("/documents/{document_id}/grants/{target_id}", status_code=204)
def revoke_grant(
    tenant_id: UUID,
    document_id: UUID,
    target_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    _owner_document(session, tenant_id, user_id, document_id, lock=True)
    session.execute(
        delete(DocumentGrant).where(
            DocumentGrant.tenant_id == tenant_id,
            DocumentGrant.document_id == document_id,
            DocumentGrant.user_id == target_id,
        )
    )
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.grant_revoked",
        resource_type="document",
        resource_id=document_id,
        details={"user_id": str(target_id)},
    )
    session.commit()
