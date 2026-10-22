"""Document ACLs are evaluated in SQL before encrypted content is loaded."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from enterprise_ai_api.models import Document, DocumentGrant, Tenant
from enterprise_ai_api.tenancy import accessible_space_ids, tenant_membership


def is_owner(session: Session, tenant_id: UUID, user_id: UUID) -> bool:
    tenant = session.get(Tenant, tenant_id)
    return bool(
        tenant
        and tenant.owner_user_id == user_id
        and tenant_membership(session, tenant_id, user_id)
    )


def document_access_predicate(
    session: Session, tenant_id: UUID, user_id: UUID, permission: str = "read"
):
    member = tenant_membership(session, tenant_id, user_id)
    if member is None:
        return false()
    spaces = accessible_space_ids(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        manage=permission != "read",
        include_document_grants=False,
    )
    inherited = and_(Document.access_mode == "space", Document.space_id.in_(spaces))
    if permission != "read" and member.role not in {"enterprise_admin", "knowledge_admin"}:
        inherited = false()
    if is_owner(session, tenant_id, user_id):
        restricted = Document.access_mode == "restricted"
    elif permission == "publish":
        restricted = false()
    else:
        grants = select(DocumentGrant.document_id).where(
            DocumentGrant.tenant_id == tenant_id,
            DocumentGrant.user_id == user_id,
        )
        if permission == "edit":
            grants = grants.where(DocumentGrant.role == "editor")
        restricted = and_(Document.access_mode == "restricted", Document.id.in_(grants))
    return and_(Document.tenant_id == tenant_id, or_(inherited, restricted))


def can_access_document(
    session: Session, tenant_id: UUID, user_id: UUID, document_id: UUID, permission: str = "read"
) -> bool:
    return (
        session.scalar(
            select(Document.id).where(
                Document.id == document_id,
                document_access_predicate(session, tenant_id, user_id, permission),
            )
        )
        is not None
    )


def authorize_document(
    session: Session, tenant_id: UUID, user_id: UUID, document_id: UUID, permission: str = "read"
) -> Document:
    document = session.scalar(
        select(Document).where(
            Document.id == document_id,
            document_access_predicate(session, tenant_id, user_id, permission),
        )
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    return document
