"""Manager-only ingestion status and explicit retry/cancellation."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.auth_routes import DatabaseSession
from enterprise_ai_api.document_routes import _version_or_404
from enterprise_ai_api.models import Document, IngestionJob
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")


def _job(session, tenant_id, user_id, version_id):
    authorize_tenant_action(
        session, tenant_id=tenant_id, user_id=user_id, action=Action.READ_KNOWLEDGE
    )
    version = _version_or_404(session, tenant_id, user_id, version_id, manage=True)
    job = session.scalar(
        select(IngestionJob).where(
            IngestionJob.tenant_id == tenant_id, IngestionJob.version_id == version_id
        )
    )
    if job is None:
        raise HTTPException(404, "This version has no asynchronous job")
    return version, job


@router.get("/tenants/{tenant_id}/document-versions/{version_id}/job")
def job_status(tenant_id: UUID, version_id: UUID, user_id: CurrentUserId, session: DatabaseSession):
    _, job = _job(session, tenant_id, user_id, version_id)
    return {
        "status": job.status,
        "attempts": job.attempts,
        "error_code": job.error_code,
        "available_at": job.available_at,
        "lease_expires_at": job.lease_expires_at,
    }


@router.post("/tenants/{tenant_id}/document-versions/{version_id}/job/{operation}")
def change_job(
    tenant_id: UUID,
    version_id: UUID,
    operation: str,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    version, job = _job(session, tenant_id, user_id, version_id)
    session.execute(update(Document).where(
        Document.id == version.document_id, Document.tenant_id == tenant_id,
    ).values(title=Document.title))
    session.expire_all()
    version, job = _job(session, tenant_id, user_id, version_id)
    if operation not in {"retry", "cancel"}:
        raise HTTPException(404, "Unknown operation")
    allowed = {"failed", "cancelled"} if operation == "retry" else {"queued", "running"}
    if job.status not in allowed or (operation == "retry" and job.error_code == "unsafe_file"):
        raise HTTPException(
            409, "Job state does not allow this operation; unsafe files require a new version"
        )
    changed = session.execute(
        update(IngestionJob)
        .where(
            IngestionJob.id == job.id,
            IngestionJob.tenant_id == tenant_id,
            IngestionJob.status.in_(allowed),
        )
        .values(
            status="queued" if operation == "retry" else "cancelled",
            attempts=0 if operation == "retry" else job.attempts,
            error_code=None if operation == "retry" else "cancelled",
            lease_token=None,
            lease_expires_at=None,
            available_at=datetime.now(UTC),
        )
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Job changed; refresh before retrying")
    version.status = "queued" if operation == "retry" else "failed"
    version.error_message = None if operation == "retry" else "cancelled"
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action=f"document.processing_{operation}",
        resource_type="document_version",
        resource_id=version_id,
    )
    session.commit()
    return {"status": version.status}
