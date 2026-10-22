"""API contracts for tenants, memberships, and knowledge spaces."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TenantRole = Literal["enterprise_admin", "knowledge_admin", "member"]
SpaceRole = Literal["manager", "viewer"]
Classification = Literal["internal", "confidential", "restricted"]
DocumentStatus = Literal["queued", "processing", "ready", "published", "superseded", "failed"]
MfaPolicy = Literal["off", "admins", "all"]


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    mfa_policy: MfaPolicy = "off"
    mfa_required_for_current_user: bool = False
    created_at: datetime


class TenantSecurityPolicyUpdate(BaseModel):
    mfa_policy: MfaPolicy


class MembershipCreate(BaseModel):
    user_id: UUID
    role: TenantRole


class MembershipStatusUpdate(BaseModel):
    active: bool


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    user_id: UUID
    role: TenantRole
    active: bool
    provisioning_active: bool
    created_at: datetime


class AccountRegister(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=200)
    organization_name: str | None = Field(default=None, min_length=2, max_length=160)


class AccountLogin(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class AccountResponse(BaseModel):
    mfa_required: Literal[False] = False
    user_id: UUID
    email: str
    tenant_id: UUID | None
    email_verified: bool
    mfa_enabled: bool
    password_login_enabled: bool = True


class MfaChallengeResponse(BaseModel):
    mfa_required: Literal[True] = True
    challenge_token: str


class MfaSetupRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]


class MfaRecoveryCodesRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=32)


class MfaLoginRequest(BaseModel):
    challenge_token: str = Field(min_length=32, max_length=200)
    code: str = Field(min_length=6, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=32)


class AuthSessionResponse(BaseModel):
    id: UUID
    client_label: str
    is_current: bool
    created_at: datetime
    expires_at: datetime


class RevokedSessionsResponse(BaseModel):
    revoked_count: int


class EmailAddressRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=12, max_length=200)


class GenericMessage(BaseModel):
    message: str


class AuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    actor_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: Literal["success", "denied", "failure"]
    details: dict[str, object]
    created_at: datetime


class InvitationCreate(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    role: TenantRole = "member"


class InvitationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    email: str
    role: TenantRole
    expires_at: datetime
    invite_token: str
    delivery_status: Literal["sent", "manual"]


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=200)


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


class SpaceMembershipCreate(BaseModel):
    user_id: UUID
    role: SpaceRole = "viewer"


class SpaceMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    space_id: UUID
    user_id: UUID
    role: SpaceRole
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
    published_version: DocumentVersionResponse | None = None


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


class DocumentContentResponse(BaseModel):
    document: DocumentResponse
    version: DocumentVersionResponse
    chunks: list[DocumentChunkResponse]


class KnowledgeQuestionCreate(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    space_ids: list[UUID] | None = Field(default=None, max_length=50)


class KnowledgeStatusResponse(BaseModel):
    semantic_enabled: bool
    generation_enabled: bool
    restricted_by_policy: bool
    published_chunks: int
    indexed_chunks: int
    embedding_model: str | None
    answer_model: str | None


class AnswerCitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    citation_number: int
    document_id: UUID
    document_version_id: UUID
    document_chunk_id: UUID
    document_title: str
    filename: str
    version_number: int
    locator: str
    excerpt: str
    relevance_score: float


class KnowledgeAnswerResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    created_by: UUID
    question: str
    answer: str
    status: Literal["grounded", "insufficient_evidence"]
    generator: Literal["local", "openai", "ollama"]
    model_name: str | None
    citations: list[AnswerCitationResponse]
    created_at: datetime
