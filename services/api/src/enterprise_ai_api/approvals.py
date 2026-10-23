"""Approval gates reuse document ACLs; approval never confers access."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select, update

from enterprise_ai_api.document_access import authorize_document, can_access_document
from enterprise_ai_api.models import (
    Document,
    DocumentApproval,
    DocumentChunk,
    DocumentVersion,
    Tenant,
    User,
)
from enterprise_ai_api.tenancy import (
    authorize_tenant_action,
    tenant_membership,
    tenant_mfa_required,
)
from enterprise_ai_core.security import Action


def lock_version(session, tenant_id, user_id, version_id, permission="edit"):
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    version = session.get(DocumentVersion, version_id)
    if version is None or version.tenant_id != tenant_id:
        raise HTTPException(404, "Resource not found or access denied")
    authorize_document(session, tenant_id, user_id, version.document_id, permission)
    session.execute(
        update(Document)
        .where(
            Document.id == version.document_id,
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
    document = authorize_document(session, tenant_id, user_id, version.document_id, permission)
    return document, session.get(DocumentVersion, version_id)


def latest_approval(session, version_id):
    return session.scalar(
        select(DocumentApproval)
        .where(
            DocumentApproval.version_id == version_id,
        )
        .order_by(DocumentApproval.attempt.desc())
        .limit(1)
    )


def assert_reviewable(session, document, version):
    if not document.approval_required:
        raise HTTPException(409, "请先由发布负责人启用此文档的发布审批。")
    if version.status not in {"ready", "superseded"} or not session.scalar(
        select(DocumentChunk.id)
        .where(
            DocumentChunk.document_version_id == version.id,
            DocumentChunk.tenant_id == document.tenant_id,
        )
        .limit(1)
    ):
        raise HTTPException(409, "仅处理完成且未发布的版本可提交或完成审批。")


def actor_still_authorized(session, tenant_id, user_id, document_id, permission):
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        return False
    tenant = session.get(Tenant, tenant_id)
    member = tenant_membership(session, tenant_id, user_id)
    # Pure check: no audit commit that could accidentally release the document lock.
    if tenant is None or member is None:
        return False
    if tenant_mfa_required(tenant, member) and user.mfa_enabled_at is None:
        return False
    return can_access_document(session, tenant_id, user_id, document_id, permission)


def consume_publication_approval(session, document, version):
    """Called under the same document lock as submit/decide/policy/ACL changes."""
    if not document.approval_required or version.status == "published":
        return
    approval = latest_approval(session, version.id)
    if approval is None or approval.status != "approved":
        raise HTTPException(409, "此版本尚未通过审批，不能发布。")
    if (
        approval.content_hash != version.content_hash
        or approval.reviewed_by in {approval.submitted_by, version.created_by}
        or not actor_still_authorized(
            session, document.tenant_id, approval.reviewed_by, document.id, "publish"
        )
        or not actor_still_authorized(
            session, document.tenant_id, approval.submitted_by, document.id, "edit"
        )
    ):
        raise HTTPException(409, "审批依据或人员权限已变化，请撤销旧审批并重新提交。")
    approval.status = "published"
    # Approval is single-use: a withdrawn/superseded version must be reviewed again.
    approval.decided_at = approval.decided_at or datetime.now(UTC)
