"""Durable bounded-retry ingestion with leases fencing stale worker writes."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update

from enterprise_ai_api.ai_providers import AIProviderError
from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.crypto import content_fingerprint, decrypt_bytes
from enterprise_ai_api.database import set_tenant_context
from enterprise_ai_api.document_processing import (
    DocumentProcessingError,
    parse_document,
    validate_extension,
)
from enterprise_ai_api.file_scanning import FileScanUnavailable, UnsafeFile
from enterprise_ai_api.models import (
    Document,
    DocumentChunk,
    DocumentVersion,
    IngestionJob,
    Space,
    User,
)
from enterprise_ai_api.object_storage import ObjectStorageError
from enterprise_ai_api.tenancy import authorize_space_access, authorize_tenant_action
from enterprise_ai_core.security import Action

MAX_ATTEMPTS = 3
LEASE_SECONDS = 900


def process_one(
    factory, storage, scanner, embeddings, tenant_id, *, allow_external_restricted=False
):
    now, token = datetime.now(UTC), uuid4().hex
    eligible = or_(
        and_(IngestionJob.status == "queued", IngestionJob.available_at <= now),
        and_(IngestionJob.status == "running", IngestionJob.lease_expires_at <= now),
    )
    with factory() as session:
        set_tenant_context(session, tenant_id)
        job = session.scalar(
            select(IngestionJob)
            .where(IngestionJob.tenant_id == tenant_id, eligible)
            .order_by(IngestionJob.available_at, IngestionJob.id)
            .limit(1)
        )
        if job is None:
            return False
        job_id, version_id, attempts = job.id, job.version_id, job.attempts + 1
        claimed = session.execute(
            update(IngestionJob)
            .execution_options(synchronize_session=False)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.tenant_id == tenant_id,
                eligible,
                IngestionJob.attempts == job.attempts,
            )
            .values(
                status="running",
                attempts=attempts,
                lease_token=token,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            )
        )
        if claimed.rowcount != 1:
            session.rollback()
            return False
        session.get(DocumentVersion, version_id).status = "processing"
        session.commit()
    chunks, vectors, error, retryable = [], None, None, False
    try:
        if attempts > MAX_ATTEMPTS:
            raise DocumentProcessingError("Attempts exhausted")
        with factory() as session:
            set_tenant_context(session, tenant_id)
            version = session.get(DocumentVersion, version_id)
            document = session.get(Document, version.document_id)
            _authorize(session, version, document)
            space = session.get(Space, document.space_id)
            external_allowed = space.classification != "restricted" or allow_external_restricted
            content = decrypt_bytes(
                storage.get(version.object_key),
                context=f"{tenant_id}:{document.id}:{version.id}".encode(),
            )
            if (
                len(content) != version.byte_size
                or content_fingerprint(content) != version.content_hash
            ):
                raise ValueError("Integrity check failed")
            extension = validate_extension(version.filename)
        scanner.scan(content)
        chunks = parse_document(content, extension)
        if embeddings.enabled and (not embeddings.external or external_allowed):
            vectors = []
            for offset in range(0, len(chunks), 64):
                with factory() as session:
                    set_tenant_context(session, tenant_id)
                    owned = session.scalar(
                        select(IngestionJob.id).where(
                            IngestionJob.id == job_id,
                            IngestionJob.tenant_id == tenant_id,
                            IngestionJob.status == "running",
                            IngestionJob.lease_token == token,
                            IngestionJob.lease_expires_at > datetime.now(UTC),
                        )
                    )
                    if owned is None:
                        raise HTTPException(403, "Job lease lost")
                    _authorize(
                        session,
                        session.get(DocumentVersion, version_id),
                        session.get(Document, document.id),
                    )
                    current_space = session.get(Space, document.space_id)
                    if (
                        embeddings.external
                        and current_space.classification == "restricted"
                        and not allow_external_restricted
                    ):
                        raise HTTPException(403, "External indexing no longer allowed")
                vectors.extend(
                    embeddings.embed([item.text for item in chunks[offset : offset + 64]])
                )
            if len(vectors) != len(chunks):
                raise AIProviderError("Incomplete embeddings")
    except UnsafeFile:
        error = "unsafe_file"
    except FileScanUnavailable:
        error, retryable = "scanner_unavailable", True
    except ObjectStorageError:
        error, retryable = "storage_unavailable", True
    except AIProviderError:
        error, retryable = "embedding_unavailable", True
    except HTTPException:
        error = "access_revoked"
    except DocumentProcessingError:
        error = "parse_failed" if attempts <= MAX_ATTEMPTS else "attempts_exhausted"
    except Exception:
        error = "processing_failed"
    with factory() as session:
        set_tenant_context(session, tenant_id)
        if error is None:
            try:
                version = session.get(DocumentVersion, version_id)
                _authorize(session, version, session.get(Document, version.document_id))
            except HTTPException:
                error = "access_revoked"
                set_tenant_context(session, tenant_id)
        retry = error is not None and retryable and attempts < MAX_ATTEMPTS
        result = session.execute(
            update(IngestionJob)
            .execution_options(synchronize_session=False)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.tenant_id == tenant_id,
                IngestionJob.status == "running",
                IngestionJob.lease_token == token,
                IngestionJob.lease_expires_at > datetime.now(UTC),
            )
            .values(
                status="queued" if retry else ("failed" if error else "succeeded"),
                error_code=error,
                lease_token=None,
                lease_expires_at=None,
                available_at=datetime.now(UTC) + timedelta(seconds=30 * attempts),
            )
        )
        if result.rowcount != 1:
            session.rollback()
            return True
        version = session.get(DocumentVersion, version_id)
        version.status = "queued" if retry else ("failed" if error else "ready")
        version.error_message = error
        if error is None:
            for position, chunk in enumerate(chunks, 1):
                vector = vectors[position - 1] if vectors is not None else None
                session.add(
                    DocumentChunk(
                        tenant_id=tenant_id,
                        document_version_id=version_id,
                        position=position,
                        locator=chunk.locator,
                        text=chunk.text,
                        embedding=json.dumps(vector) if vector is not None else None,
                        embedding_model=embeddings.model_name if vector is not None else None,
                    )
                )
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=version.created_by,
            action="document.processing_completed",
            resource_type="document_version",
            resource_id=version_id,
            outcome="failure" if error else "success",
            details={
                "attempt": attempts,
                "error_code": error,
                "retry_scheduled": retry,
                "scanner": scanner.name,
            },
        )
        session.commit()
    return True


def _authorize(session, version, document):
    user = session.get(User, version.created_by)
    if user is None or user.status != "active":
        raise HTTPException(403, "Access revoked")
    authorize_tenant_action(
        session,
        tenant_id=version.tenant_id,
        user_id=version.created_by,
        action=Action.MANAGE_DOCUMENTS,
    )
    authorize_space_access(
        session,
        tenant_id=version.tenant_id,
        user_id=version.created_by,
        space_id=document.space_id,
        manage=True,
    )
