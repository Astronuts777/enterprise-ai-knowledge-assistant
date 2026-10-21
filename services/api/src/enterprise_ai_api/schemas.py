"""API contracts for tenants, memberships, and knowledge spaces."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TenantRole = Literal["enterprise_admin", "knowledge_admin", "member"]
Classification = Literal["internal", "confidential", "restricted"]


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
