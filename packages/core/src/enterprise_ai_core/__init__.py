"""Shared domain rules for the enterprise AI knowledge assistant."""

from .security import (
    AccessContext,
    Action,
    PermissionDenied,
    ResourceScope,
    Role,
    TenantBoundaryViolation,
    authorize,
)

__all__ = [
    "AccessContext",
    "Action",
    "PermissionDenied",
    "ResourceScope",
    "Role",
    "TenantBoundaryViolation",
    "authorize",
]
