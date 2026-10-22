"""Enterprise administrator access to tenant-scoped audit events."""

import csv
import json
from datetime import datetime
from io import StringIO
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session
from enterprise_ai_api.models import AuditEvent
from enterprise_ai_api.schemas import AuditEventResponse
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]
AuditOutcome = Literal["success", "denied", "failure"]
MAX_AUDIT_EXPORT_ROWS = 5_000


def _filtered_audit_statement(
    tenant_id: UUID,
    *,
    before: datetime | None = None,
    after: datetime | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    outcome: AuditOutcome | None = None,
):
    if before is not None and after is not None and after >= before:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The 'from' timestamp must be earlier than 'to'",
        )
    statement = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if before is not None:
        statement = statement.where(AuditEvent.created_at < before)
    if after is not None:
        statement = statement.where(AuditEvent.created_at >= after)
    if action:
        statement = statement.where(AuditEvent.action == action)
    if resource_type:
        statement = statement.where(AuditEvent.resource_type == resource_type)
    if outcome:
        statement = statement.where(AuditEvent.outcome == outcome)
    return statement


def _spreadsheet_safe(value: object | None) -> str:
    """Prevent exported cells from being interpreted as spreadsheet formulas."""

    rendered = "" if value is None else str(value)
    if rendered.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return f"'{rendered}"
    return rendered


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
    outcome: AuditOutcome | None = None,
) -> list[AuditEvent]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.VIEW_AUDIT,
    )
    statement = _filtered_audit_statement(
        tenant_id,
        before=before,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
    )
    return list(
        session.scalars(
            statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        )
    )


@router.get("/tenants/{tenant_id}/audit-events/export.csv")
def export_audit_events(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    after: Annotated[datetime | None, Query(alias="from")] = None,
    before: Annotated[datetime | None, Query(alias="to")] = None,
    action: Annotated[str | None, Query(max_length=80)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    outcome: AuditOutcome | None = None,
) -> Response:
    """Export a bounded, tenant-scoped audit snapshot for compliance review."""

    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.VIEW_AUDIT,
    )
    statement = _filtered_audit_statement(
        tenant_id,
        before=before,
        after=after,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
    )
    events = list(
        session.scalars(
            statement.order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc()).limit(
                MAX_AUDIT_EXPORT_ROWS + 1
            )
        )
    )
    if len(events) > MAX_AUDIT_EXPORT_ROWS:
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="audit.export_failed",
            resource_type="audit_export",
            outcome="failure",
            details={"reason": "row_limit_exceeded", "limit": MAX_AUDIT_EXPORT_ROWS},
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audit export exceeds 5000 rows; narrow the time range or filters",
        )

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "created_at",
            "event_id",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "outcome",
            "details_json",
        ]
    )
    for event in events:
        writer.writerow(
            [
                event.created_at.isoformat(),
                event.id,
                event.actor_id,
                _spreadsheet_safe(event.action),
                _spreadsheet_safe(event.resource_type),
                _spreadsheet_safe(event.resource_id),
                event.outcome,
                json.dumps(
                    event.details,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ]
        )

    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="audit.exported",
        resource_type="audit_export",
        outcome="success",
        details={
            "row_count": len(events),
            "filtered_by_time": after is not None or before is not None,
            "filtered_by_action": action is not None,
            "filtered_by_resource_type": resource_type is not None,
            "filtered_by_outcome": outcome is not None,
        },
    )
    session.commit()

    filename = f"audit-{tenant_id}-{datetime.now().date().isoformat()}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
