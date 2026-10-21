"""Fail-closed tenant and role authorization rules."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    ENTERPRISE_ADMIN = "enterprise_admin"
    KNOWLEDGE_ADMIN = "knowledge_admin"
    MEMBER = "member"
    OWNER = "owner"
    APPROVER = "approver"


class Action(StrEnum):
    READ_KNOWLEDGE = "read_knowledge"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SPACES = "manage_spaces"
    MANAGE_DOCUMENTS = "manage_documents"
    CREATE_DECISION_PACK = "create_decision_pack"
    REVIEW_DECISION_PACK = "review_decision_pack"
    VIEW_AUDIT = "view_audit"


class AuthorizationError(Exception):
    """Base class for deliberately non-specific authorization failures."""


class TenantBoundaryViolation(AuthorizationError):
    """Raised before role evaluation when tenant boundaries do not match."""


class PermissionDenied(AuthorizationError):
    """Raised when no assigned role grants the requested action."""


@dataclass(frozen=True, slots=True)
class AccessContext:
    tenant_id: UUID
    user_id: UUID
    roles: frozenset[Role]


@dataclass(frozen=True, slots=True)
class ResourceScope:
    tenant_id: UUID


_ALL_ACTIONS = frozenset(Action)

_ROLE_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.ENTERPRISE_ADMIN: _ALL_ACTIONS,
    Role.KNOWLEDGE_ADMIN: frozenset(
        {Action.READ_KNOWLEDGE, Action.MANAGE_SPACES, Action.MANAGE_DOCUMENTS}
    ),
    Role.MEMBER: frozenset({Action.READ_KNOWLEDGE}),
    Role.OWNER: frozenset({Action.READ_KNOWLEDGE, Action.CREATE_DECISION_PACK}),
    Role.APPROVER: frozenset({Action.READ_KNOWLEDGE, Action.REVIEW_DECISION_PACK}),
}


def authorize(context: AccessContext, resource: ResourceScope, action: Action) -> None:
    """Authorize an action without leaking cross-tenant resource details.

    Tenant equality is checked before roles. A highly privileged role in one tenant
    never grants access to another tenant.
    """

    if context.tenant_id != resource.tenant_id:
        raise TenantBoundaryViolation("Resource not found or access denied")

    granted = set[Action]()
    for role in context.roles:
        granted.update(_ROLE_PERMISSIONS.get(role, frozenset()))

    if action not in granted:
        raise PermissionDenied("Resource not found or access denied")

