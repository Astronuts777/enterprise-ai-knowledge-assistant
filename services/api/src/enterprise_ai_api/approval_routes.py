"""Version-specific submissions, decisions, cancellation, and a permission-filtered inbox."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.approvals import (
    actor_still_authorized,
    assert_reviewable,
    latest_approval,
    lock_version,
)
from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session
from enterprise_ai_api.document_access import can_access_document, document_access_predicate
from enterprise_ai_api.models import Document, DocumentApproval, DocumentVersion
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1/tenants/{tenant_id}")
DatabaseSession = Annotated[Session, Depends(get_session)]
ApprovalStatus = Literal["pending", "approved", "rejected", "cancelled", "published"]


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(default="", max_length=2000)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=2000)


class ApprovalResponse(BaseModel):
    id: UUID
    document_id: UUID
    version_id: UUID
    document_title: str
    version_number: int
    attempt: int
    status: ApprovalStatus
    submitted_by: UUID
    submission_note: str
    reviewed_by: UUID | None
    review_note: str | None
    created_at: datetime
    decided_at: datetime | None
    can_decide: bool
    can_cancel: bool


def _response(session, approval, document, version, user_id):
    publisher = can_access_document(
        session,
        document.tenant_id,
        user_id,
        document.id,
        "publish",
    )
    return ApprovalResponse(
        id=approval.id,
        document_id=document.id,
        version_id=version.id,
        document_title=document.title,
        version_number=version.version_number,
        attempt=approval.attempt,
        status=approval.status,
        submitted_by=approval.submitted_by,
        submission_note=approval.submission_note,
        reviewed_by=approval.reviewed_by,
        review_note=approval.review_note,
        created_at=approval.created_at,
        decided_at=approval.decided_at,
        can_decide=(
            approval.status == "pending"
            and publisher
            and user_id not in {approval.submitted_by, version.created_by}
        ),
        can_cancel=(
            approval.status in {"pending", "approved"}
            and (publisher or approval.submitted_by == user_id)
        ),
    )


@router.get("/approvals", response_model=list[ApprovalResponse])
def inbox(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    status: ApprovalStatus | None = None,
    version_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    statement = (
        select(DocumentApproval, Document, DocumentVersion)
        .join(
            Document,
            Document.id == DocumentApproval.document_id,
        )
        .join(DocumentVersion, DocumentVersion.id == DocumentApproval.version_id)
        .where(
            DocumentApproval.tenant_id == tenant_id,
            DocumentVersion.tenant_id == tenant_id,
            DocumentVersion.document_id == Document.id,
            document_access_predicate(session, tenant_id, user_id, "edit"),
        )
    )
    if status is not None:
        statement = statement.where(DocumentApproval.status == status)
    if version_id is not None:
        statement = statement.where(DocumentApproval.version_id == version_id)
        statement = statement.order_by(DocumentApproval.attempt.desc())
    rows = session.execute(
        statement.order_by(
            DocumentApproval.created_at.desc(),
            DocumentApproval.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    return [
        _response(session, item, document, version, user_id) for item, document, version in rows
    ]


@router.post("/document-versions/{version_id}/enable-approval")
def enable_approval(
    tenant_id: UUID, version_id: UUID, user_id: CurrentUserId, session: DatabaseSession
):
    document, _ = lock_version(session, tenant_id, user_id, version_id, "publish")
    if not document.approval_required:
        document.approval_required = True
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="document.approval_enabled",
            resource_type="document",
            resource_id=document.id,
        )
    session.commit()
    return {"approval_required": True}


@router.post(
    "/document-versions/{version_id}/approvals", response_model=ApprovalResponse, status_code=201
)
def submit(
    tenant_id: UUID,
    version_id: UUID,
    payload: Submission,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    document, version = lock_version(session, tenant_id, user_id, version_id)
    assert_reviewable(session, document, version)
    previous = latest_approval(session, version_id)
    if previous is not None and previous.status in {"pending", "approved"}:
        raise HTTPException(409, "此版本已有待处理或已通过的审批，请勿重复提交。")
    approval = DocumentApproval(
        tenant_id=tenant_id,
        document_id=document.id,
        version_id=version_id,
        attempt=previous.attempt + 1 if previous else 1,
        content_hash=version.content_hash,
        submitted_by=user_id,
        submission_note=payload.note.strip(),
        status="pending",
    )
    session.add(approval)
    session.flush()
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.approval_submitted",
        resource_type="document_approval",
        resource_id=approval.id,
        details={"document_id": str(document.id), "version_id": str(version_id)},
    )
    result = _response(session, approval, document, version, user_id)
    session.commit()
    return result


def _lock_request(session, tenant_id, user_id, approval_id, permission):
    # Resolve IDs only. Decrypt notes only after current tenant/document ACL checks.
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    version_id = session.scalar(
        select(DocumentApproval.version_id).where(
            DocumentApproval.id == approval_id,
            DocumentApproval.tenant_id == tenant_id,
        )
    )
    if version_id is None:
        raise HTTPException(404, "Resource not found or access denied")
    document, version = lock_version(session, tenant_id, user_id, version_id, permission)
    approval = session.get(DocumentApproval, approval_id)
    return approval, document, version


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalResponse)
def decide(
    tenant_id: UUID,
    approval_id: UUID,
    payload: Decision,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    approval, document, version = _lock_request(
        session,
        tenant_id,
        user_id,
        approval_id,
        "publish",
    )
    if approval.status != "pending":
        raise HTTPException(409, "审批状态已变化，请刷新后重试。")
    if user_id in {approval.submitted_by, version.created_by}:
        raise HTTPException(403, "提交者和版本上传者不能审批自己的版本。")
    if not payload.note.strip():
        raise HTTPException(422, "请填写审批意见。")
    assert_reviewable(session, document, version)
    if payload.decision == "approved" and (
        approval.content_hash != version.content_hash
        or not actor_still_authorized(
            session, tenant_id, approval.submitted_by, document.id, "edit"
        )
    ):
        raise HTTPException(409, "版本或提交者权限已变化，请取消并重新提交。")
    approval.status = payload.decision
    approval.reviewed_by = user_id
    approval.review_note = payload.note.strip()
    approval.decided_at = datetime.now(UTC)
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action=f"document.approval_{payload.decision}",
        resource_type="document_approval",
        resource_id=approval.id,
        details={"document_id": str(document.id), "version_id": str(version.id)},
    )
    result = _response(session, approval, document, version, user_id)
    session.commit()
    return result


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalResponse)
def cancel(tenant_id: UUID, approval_id: UUID, user_id: CurrentUserId, session: DatabaseSession):
    approval, document, version = _lock_request(
        session,
        tenant_id,
        user_id,
        approval_id,
        "edit",
    )
    if user_id != approval.submitted_by and not can_access_document(
        session,
        tenant_id,
        user_id,
        document.id,
        "publish",
    ):
        raise HTTPException(403, "Only the submitter or publication manager can cancel")
    if approval.status not in {"pending", "approved"}:
        raise HTTPException(409, "审批状态已变化，请刷新后重试。")
    approval.status = "cancelled"
    approval.decided_at = datetime.now(UTC)
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.approval_cancelled",
        resource_type="document_approval",
        resource_id=approval.id,
    )
    result = _response(session, approval, document, version, user_id)
    session.commit()
    return result
