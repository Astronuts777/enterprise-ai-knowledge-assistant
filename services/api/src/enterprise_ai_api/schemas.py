"""API contracts for tenants, memberships, and knowledge spaces."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TenantRole = Literal["enterprise_admin", "knowledge_admin", "member"]
Classification = Literal["internal", "confidential", "restricted"]
DocumentStatus = Literal["ready", "published", "superseded", "failed"]


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    created_at: datetime


class MembershipCreate(BaseModel):
    user_id: UUID
    role: TenantRole


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    created_at: datetime


class SpaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    classification: Classification = "internal"


class SpaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    classification: Classification
    created_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    space_id: UUID
    title: str
    created_by: UUID
    created_at: datetime


class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    document_id: UUID
    version_number: int
    filename: str
    media_type: str
    byte_size: int
    content_hash: str
    status: DocumentStatus
    error_message: str | None
    created_by: UUID
    created_at: datetime
    published_at: datetime | None


class DocumentSummary(BaseModel):
    document: DocumentResponse
    latest_version: DocumentVersionResponse


class DocumentDetail(BaseModel):
    document: DocumentResponse
    versions: list[DocumentVersionResponse]


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    version: DocumentVersionResponse


class DocumentChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_version_id: UUID
    position: int
    locator: str
    text: str
