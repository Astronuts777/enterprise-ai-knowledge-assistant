"""Permission-aware document upload, version, preview, and publication endpoints."""

import mimetypes
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from enterprise_ai_api.crypto import content_fingerprint, encrypt_bytes
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.document_processing import (
    MAX_UPLOAD_BYTES,
    DocumentProcessingError,
    parse_document,
    safe_filename,
    validate_extension,
)
from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion, Space
from enterprise_ai_api.schemas import (
    DocumentChunkResponse,
    DocumentDetail,
    DocumentSummary,
    DocumentUploadResponse,
    DocumentVersionResponse,
)
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]
UploadedFile = Annotated[UploadFile, File(description="PDF, DOCX, TXT, or Markdown; max 10 MiB")]
DocumentTitle = Annotated[str | None, Form(max_length=255)]


def get_storage_root(request: Request) -> Path:
    return request.app.state.storage_root


StorageRoot = Annotated[Path, Depends(get_storage_root)]


def _space_or_404(session: Session, tenant_id: UUID, space_id: UUID) -> Space:
    space = session.get(Space, space_id)
    if space is None or space.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    return space


def _document_or_404(session: Session, tenant_id: UUID, document_id: UUID) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    return document


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


def _add_version(
    session: Session,
    *,
    storage_root: Path,
    tenant_id: UUID,
    document: Document,
    user_id: UUID,
    filename: str,
    extension: str,
    media_type: str | None,
    content: bytes,
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
    object_key = str(Path(str(tenant_id)) / str(document.id) / str(version_id) / "payload.enc")
    target = storage_root / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    encryption_context = f"{tenant_id}:{document.id}:{version_id}".encode()
    target.write_bytes(encrypt_bytes(content, context=encryption_context))

    try:
        parsed_chunks = parse_document(content, extension)
        version_status = "ready"
        error_message = None
    except DocumentProcessingError as error:
        parsed_chunks = []
        version_status = "failed"
        error_message = str(error)

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
    for position, chunk in enumerate(parsed_chunks, start=1):
        session.add(
            DocumentChunk(
                tenant_id=tenant_id,
                document_version_id=version_id,
                position=position,
                locator=chunk.locator,
                text=chunk.text,
            )
        )

    try:
        session.commit()
    except Exception:
        session.rollback()
        target.unlink(missing_ok=True)
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
    storage_root: StorageRoot,
    file: UploadedFile,
    title: DocumentTitle = None,
) -> DocumentUploadResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    _space_or_404(session, tenant_id, space_id)
    filename, extension, content = await _read_upload(file)
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
    version = _add_version(
        session,
        storage_root=storage_root,
        tenant_id=tenant_id,
        document=document,
        user_id=user_id,
        filename=filename,
        extension=extension,
        media_type=file.content_type,
        content=content,
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
    storage_root: StorageRoot,
    file: UploadedFile,
) -> DocumentVersion:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_DOCUMENTS,
    )
    document = _document_or_404(session, tenant_id, document_id)
    filename, extension, content = await _read_upload(file)
    return _add_version(
        session,
        storage_root=storage_root,
        tenant_id=tenant_id,
        document=document,
        user_id=user_id,
        filename=filename,
        extension=extension,
        media_type=file.content_type,
        content=content,
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
    _space_or_404(session, tenant_id, space_id)
    documents = session.scalars(
        select(Document)
        .where(Document.tenant_id == tenant_id, Document.space_id == space_id)
        .order_by(Document.created_at.desc(), Document.id)
    )
    summaries: list[DocumentSummary] = []
    for document in documents:
        latest = session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        if latest is not None:
            summaries.append(DocumentSummary(document=document, latest_version=latest))
    return summaries


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
    document = _document_or_404(session, tenant_id, document_id)
    versions = list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version_number.desc())
        )
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
    version = session.get(DocumentVersion, version_id)
    if version is None or version.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
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
    version = session.get(DocumentVersion, version_id)
    if version is None or version.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    if version.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A failed document version cannot be published",
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
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(version)
    return version


def storage_root_from_environment() -> Path:
    configured = os.getenv("LOCAL_STORAGE_PATH")
    return Path(configured).resolve() if configured else Path("data/local/uploads").resolve()
