"""Append-only, metadata-only security audit helpers."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from enterprise_ai_api.models import AuditEvent

AuditScalar = str | int | float | bool | None


def record_audit_event(
    session: Session,
    *,
    tenant_id: UUID,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None = None,
    outcome: str = "success",
    details: Mapping[str, AuditScalar] | None = None,
) -> AuditEvent:
    """Stage an event in the same transaction as the business operation.

    Callers must only provide non-sensitive metadata: never email addresses,
    document text, user questions, answers, tokens, or file object keys.
    """

    event = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        details=dict(details or {}),
    )
    session.add(event)
    return event
