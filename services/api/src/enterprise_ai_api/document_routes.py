"""Permission-aware document upload, version, preview, and publication endpoints."""

import json
import logging
import mimetypes
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from enterprise_ai_api.ai_providers import AIProviderError, EmbeddingProvider
from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.crypto import content_fingerprint, encrypt_bytes
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.document_processing import (
    MAX_UPLOAD_BYTES,
    DocumentProcessingError,
    parse_document,
    safe_filename,
    validate_extension,
)
from enterprise_ai_api.file_scanning import FileScanUnavailable, UnsafeFile
from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion, IngestionJob, Space
from enterprise_ai_api.object_storage import ObjectStorage, ObjectStorageError
from enterprise_ai_api.schemas import (
    DocumentChunkResponse,
    DocumentContentResponse,
    DocumentDetail,
    DocumentSummary,
    DocumentUploadResponse,
    DocumentVersionResponse,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    authorize_space_access,
    authorize_tenant_action,
    can_manage_space,
)
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)
DatabaseSession = Annotated[Session, Depends(get_session)]
UploadedFile = Annotated[UploadFile, File(description="PDF, DOCX, TXT, or Markdown; max 10 MiB")]
DocumentTitle = Annotated[str | None, Form(max_length=255)]


def get_object_storage(request: Request) -> ObjectStorage:
    return request.app.state.object_storage


Storage = Annotated[ObjectStorage, Depends(get_object_storage)]


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return request.app.state.embedding_provider


Embedding = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


def _document_or_404(
    session: Session,
    tenant_id: UUID,
    user_id: UUID,
    document_id: UUID,
    *,
    manage: bool,
) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=document.space_id,
        manage=manage,
    )
    return document


def _version_or_404(
    session: Session,
    tenant_id: UUID,
    user_id: UUID,
    version_id: UUID,
    *,
    manage: bool,
) -> DocumentVersion:
    version = session.get(DocumentVersion, version_id)
    if version is None or version.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    _document_or_404(
        session,
        tenant_id,
        user_id,
        version.document_id,
        manage=manage,
    )
    return version


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes]:
    filename = safe_filename(file.filename)
    try:
        extension = validate_extension(filename)
    except DocumentProcessingError as error:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(error)
        ) from error
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded file is empty",
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The uploaded file exceeds the 10 MiB limit",
        )
    return filename, extension, content


async def _scan_upload(
    request: Request,
    session: Session,
    tenant_id: UUID,
    user_id: UUID,
    space_id: UUID,
    content: bytes,
) -> str:
    if request.app.state.async_ingestion:
        return "pending"
    scanner = request.app.state.file_scanner
    try:
        await run_in_threadpool(scanner.scan, content)
    except (UnsafeFile, FileScanUnavailable) as error:
        unsafe = isinstance(error, UnsafeFile)
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="document.upload_blocked",
            resource_type="space",
            resource_id=space_id,
            outcome="denied",
            details={"reason": "unsafe_file" if unsafe else "scanner_unavailable"},
        )
        session.commit()
        raise HTTPException(
            status_code=422 if unsafe else 503,
            detail="文件安全扫描发现风险，上传已拒绝。"
            if unsafe
            else "文件安全扫描暂不可用，上传已阻止，请稍后重试。",
        ) from error
    return "clean" if scanner.name != "disabled" else "not_scanned"


def _add_version(
    session: Session,
    *,
    object_storage: ObjectStorage,
    tenant_id: UUID,
    document: Document,
    user_id: UUID,
    filename: str,
    extension: str,
    media_type: str | None,
    content: bytes,
    audit_action: str,
    embedding_provider: EmbeddingProvider,
    allow_external_ai: bool,
    scan_status: str,
) -> DocumentVersion:
    content_hash = content_fingerprint(content)
    duplicate = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.content_hash == content_hash,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This exact file is already a version of the document",
        )

    latest_number = session.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document.id
        )
    )
    version_id = uuid4()
    version_number = (latest_number or 0) + 1
    object_key = str(
        PurePosixPath(str(tenant_id), str(document.id), str(version_id), "payload.enc")
    )
    encryption_context = f"{tenant_id}:{document.id}:{version_id}".encode()
    try:
        object_storage.put(object_key, encrypt_bytes(content, context=encryption_context))
    except ObjectStorageError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Encrypted object storage is temporarily unavailable",
        ) from error

    try:
        parsed_chunks = [] if scan_status == "pending" else parse_document(content, extension)
        version_status = "queued" if scan_status == "pending" else "ready"
        error_message = None
    except DocumentProcessingError as error:
        parsed_chunks = []
        version_status = "failed"
        error_message = str(error)

    embeddings: list[list[float]] | None = None
    embedding_status = "disabled"
    if parsed_chunks and embedding_provider.enabled:
        if embedding_provider.external and not allow_external_ai:
            embedding_status = "blocked_by_classification"
        else:
            try:
                embeddings = []
                for start in range(0, len(parsed_chunks), 64):
                    batch = parsed_chunks[start : start + 64]
                    embeddings.extend(embedding_provider.embed([chunk.text for chunk in batch]))
                if len(embeddings) != len(parsed_chunks):
                    raise AIProviderError("Embedding provider returned incomplete results")
                embedding_status = "indexed"
            except AIProviderError:
                logger.warning(
                    "Semantic indexing failed for document version %s",
                    version_id,
                    exc_info=True,
                )
                embeddings = None
                embedding_status = "unavailable"

    version = DocumentVersion(
        id=version_id,
        tenant_id=tenant_id,
        document_id=document.id,
        version_number=version_number,
        filename=filename,
        media_type=media_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        byte_size=len(content),
        content_hash=content_hash,
        object_key=object_key,
        status=version_status,
        error_message=error_message,
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    if version_status == "queued":
        session.add(
            IngestionJob(tenant_id=tenant_id, version_id=version_id, available_at=datetime.now(UTC))
        )
    for position, chunk in enumerate(parsed_chunks, start=1):
        vector = embeddings[position - 1] if embeddings is not None else None
        session.add(
            DocumentChunk(
                tenant_id=tenant_id,
                document_version_id=version_id,
                position=position,
                locator=chunk.locator,
                text=chunk.text,
                embedding=(
                    json.dumps(vector, separators=(",", ":")) if vector is not None else None
                ),
                embedding_model=(embedding_provider.model_name if vector is not None else None),
            )
        )

    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action=audit_action,
        resource_type="document_version",
        resource_id=version_id,
        details={
            "document_id": str(document.id),
            "space_id": str(document.space_id),
            "version_number": version_number,
            "byte_size": len(content),
            "status": version_status,
            "embedding_status": embedding_status,
            "embedding_provider": embedding_provider.name,
            "file_scan_status": scan_status,
        },
    )

    try:
        session.commit()
    except Exception:
        session.rollback()
        with suppress(ObjectStorageError):
            object_storage.delete(object_key)
        raise
    set_tenant_context(session, tenant_id)
    session.refresh(version)
    return version


@router.post(
    "/tenants/{tenant_id}/spaces/{space_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    tenant_id: UUID,
    space_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    object_storage: Storage,
    embedding_provider: Embedding,
    request: Request,
    file: UploadedFile,
    title: DocumentTitle = None,
) -> DocumentUploadResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
        manage=True,
    )
    filename, extension, content = await _read_upload(file)
    scan_status = await _scan_upload(request, session, tenant_id, user_id, space_id, content)
    document_title = (title or Path(filename).stem).strip()
    if not document_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Title is required",
        )

    document = Document(
        tenant_id=tenant_id,
        space_id=space_id,
        title=document_title,
        created_by=user_id,
    )
    session.add(document)
    session.flush()
    space = session.get(Space, space_id)
    allow_external_ai = bool(
        space is not None
        and (
            space.classification != "restricted"
            or request.app.state.external_ai_allowed_for_restricted
        )
    )
    version = _add_version(
        session,
        object_storage=object_storage,
        tenant_id=tenant_id,
        document=document,
        user_id=user_id,
        filename=filename,
        extension=extension,
        media_type=file.content_type,
        content=content,
        audit_action="document.created",
        embedding_provider=embedding_provider,
        allow_external_ai=allow_external_ai,
        scan_status=scan_status,
    )
    return DocumentUploadResponse(document=document, version=version)


@router.post(
    "/tenants/{tenant_id}/documents/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document_version(
    tenant_id: UUID,
    document_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    object_storage: Storage,
    embedding_provider: Embedding,
    request: Request,
    file: UploadedFile,
) -> DocumentVersion:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    document = _document_or_404(session, tenant_id, user_id, document_id, manage=True)
    filename, extension, content = await _read_upload(file)
    scan_status = await _scan_upload(
        request, session, tenant_id, user_id, document.space_id, content
    )
    space = session.get(Space, document.space_id)
    allow_external_ai = bool(
        space is not None
        and (
            space.classification != "restricted"
            or request.app.state.external_ai_allowed_for_restricted
        )
    )
    return _add_version(
        session,
        object_storage=object_storage,
        tenant_id=tenant_id,
        document=document,
        user_id=user_id,
        filename=filename,
        extension=extension,
        media_type=file.content_type,
        content=content,
        audit_action="document.version_uploaded",
        embedding_provider=embedding_provider,
        allow_external_ai=allow_external_ai,
        scan_status=scan_status,
    )


@router.get(
    "/tenants/{tenant_id}/spaces/{space_id}/documents",
    response_model=list[DocumentSummary],
)
def list_documents(
    tenant_id: UUID,
    space_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> list[DocumentSummary]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
        manage=False,
    )
    documents = session.scalars(
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.space_id == space_id)
        .order_by(Document.created_at.desc(), Document.id)
    )
    summaries: list[DocumentSummary] = []
    can_manage = can_manage_space(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=space_id,
    )
    for document in documents:
        version_statement = select(DocumentVersion).where(
            DocumentVersion.document_id == document.id
        )
        if not can_manage:
            version_statement = version_statement.where(DocumentVersion.status == "published")
        latest = session.scalar(
            version_statement.order_by(DocumentVersion.version_number.desc()).limit(1)
        )
        if latest is not None:
            summaries.append(DocumentSummary(document=document, latest_version=latest))
    return summaries


@router.get(
    "/tenants/{tenant_id}/documents/{document_id}/content",
    response_model=DocumentContentResponse,
)
def read_document_content(
    tenant_id: UUID,
    document_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> DocumentContentResponse:
    """Return parsed content only after tenant, space, and publication checks."""

    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    document = _document_or_404(session, tenant_id, user_id, document_id, manage=False)
    can_manage = can_manage_space(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=document.space_id,
    )
    version_statement = select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    if not can_manage:
        version_statement = version_statement.where(DocumentVersion.status == "published")
    version = session.scalar(
        version_statement.order_by(DocumentVersion.version_number.desc()).limit(1)
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version.id)
            .order_by(DocumentChunk.position)
        )
    )
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.viewed",
        resource_type="document",
        resource_id=document.id,
        details={
            "space_id": str(document.space_id),
            "version_number": version.version_number,
            "version_status": version.status,
            "access_level": "manager" if can_manage else "viewer",
        },
    )
    response = DocumentContentResponse(
        document=document,
        version=version,
        chunks=chunks,
    )
    session.commit()
    return response


@router.get("/tenants/{tenant_id}/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    tenant_id: UUID,
    document_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> DocumentDetail:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    document = _document_or_404(session, tenant_id, user_id, document_id, manage=False)
    can_manage = can_manage_space(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=document.space_id,
    )
    version_statement = select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    if not can_manage:
        version_statement = version_statement.where(DocumentVersion.status == "published")
    versions = list(
        session.scalars(version_statement.order_by(DocumentVersion.version_number.desc()))
    )
    return DocumentDetail(document=document, versions=versions)


@router.get(
    "/tenants/{tenant_id}/document-versions/{version_id}/chunks",
    response_model=list[DocumentChunkResponse],
)
def preview_document_chunks(
    tenant_id: UUID,
    version_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> list[DocumentChunk]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    _version_or_404(session, tenant_id, user_id, version_id, manage=True)
    return list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
            .order_by(DocumentChunk.position)
        )
    )


@router.post(
    "/tenants/{tenant_id}/document-versions/{version_id}/publish",
    response_model=DocumentVersionResponse,
)
def publish_document_version(
    tenant_id: UUID,
    version_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> DocumentVersion:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    version = _version_or_404(session, tenant_id, user_id, version_id, manage=True)
    if version.status in {"failed", "queued", "processing"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only successfully processed document versions can be published",
        )

    published_versions = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == version.document_id,
            DocumentVersion.status == "published",
            DocumentVersion.id != version.id,
        )
    )
    for previous in published_versions:
        previous.status = "superseded"
    version.status = "published"
    version.published_at = datetime.now(UTC)
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.published",
        resource_type="document_version",
        resource_id=version.id,
        details={
            "document_id": str(version.document_id),
            "version_number": version.version_number,
        },
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(version)
    return version


@router.post(
    "/tenants/{tenant_id}/document-versions/{version_id}/semantic-index",
    response_model=DocumentVersionResponse,
)
def rebuild_document_semantic_index(
    tenant_id: UUID,
    version_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    embedding_provider: Embedding,
    request: Request,
) -> DocumentVersion:
    """Create or replace encrypted embeddings for an existing document version."""

    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    version = _version_or_404(session, tenant_id, user_id, version_id, manage=True)
    if not embedding_provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Semantic indexing is not configured",
        )
    document = session.get(Document, version.document_id)
    space = session.get(Space, document.space_id) if document is not None else None
    if (
        embedding_provider.external
        and space is not None
        and space.classification == "restricted"
        and not request.app.state.external_ai_allowed_for_restricted
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="External semantic indexing is blocked for restricted spaces",
        )
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
            .order_by(DocumentChunk.position)
        )
    )
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document version has no parsed content",
        )
    try:
        embeddings: list[list[float]] = []
        for start in range(0, len(chunks), 64):
            batch = chunks[start : start + 64]
            embeddings.extend(embedding_provider.embed([chunk.text for chunk in batch]))
        if len(embeddings) != len(chunks):
            raise AIProviderError("Embedding provider returned incomplete results")
    except AIProviderError as error:
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="document.semantic_index_failed",
            resource_type="document_version",
            resource_id=version.id,
            outcome="failure",
            details={
                "provider": embedding_provider.name,
                "model": embedding_provider.model_name,
            },
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semantic indexing is temporarily unavailable",
        ) from error

    for chunk, vector in zip(chunks, embeddings, strict=True):
        chunk.embedding = json.dumps(vector, separators=(",", ":"))
        chunk.embedding_model = embedding_provider.model_name
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="document.semantic_indexed",
        resource_type="document_version",
        resource_id=version.id,
        details={
            "chunk_count": len(chunks),
            "provider": embedding_provider.name,
            "model": embedding_provider.model_name,
        },
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(version)
    return version
