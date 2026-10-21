"""Enterprise administrator access to tenant-scoped audit events."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.database import get_session
from enterprise_ai_api.models import AuditEvent
from enterprise_ai_api.schemas import AuditEventResponse
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get(
    "/tenants/{tenant_id}/audit-events",
    response_model=list[AuditEventResponse],
)
def list_audit_events(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: datetime | None = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    outcome: Literal["success", "denied", "failure"] | None = None,
) -> list[AuditEvent]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.VIEW_AUDIT,
    )
    statement = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if before is not None:
        statement = statement.where(AuditEvent.created_at < before)
    if action:
        statement = statement.where(AuditEvent.action == action)
    if resource_type:
        statement = statement.where(AuditEvent.resource_type == resource_type)
    if outcome:
        statement = statement.where(AuditEvent.outcome == outcome)
    return list(
        session.scalars(
            statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        )
    )
