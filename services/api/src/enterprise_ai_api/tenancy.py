"""Tenant-scoped authorization and identity adapters."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.crypto import token_hash
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import AuthSession, Membership, Tenant, User
from enterprise_ai_core.security import (
    AccessContext,
    Action,
    PermissionDenied,
    ResourceScope,
    Role,
    authorize,
)

SESSION_COOKIE_NAME = "enterprise_ai_demo_session"


def sign_demo_session(user_id: UUID, secret: str) -> str:
    payload = str(user_id)
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def verify_demo_session(token: str | None, secret: str) -> UUID | None:
    if not token:
        return None
    try:
        payload, encoded_signature = token.rsplit(".", 1)
        user_id = UUID(payload)
    except (ValueError, TypeError):
        return None
    expected = sign_demo_session(user_id, secret).rsplit(".", 1)[1]
    if not hmac.compare_digest(encoded_signature, expected):
        return None
    return user_id


def current_user_id(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    development_user_id: Annotated[UUID | None, Header(alias="X-Dev-User-Id")] = None,
    demo_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    account_session: Annotated[
        str | None, Cookie(alias="enterprise_ai_session")
    ] = None,
) -> UUID:
    """Resolve a local dev header or a server-signed public-demo browser session."""

    if request.app.state.account_auth:
        if account_session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        stored = session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash(account_session))
        )
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        expires_at = stored.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        user = session.get(User, stored.user_id)
        if expires_at <= datetime.now(UTC) or user is None or user.status != "active":
            session.delete(stored)
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        return stored.user_id
    if request.app.state.public_demo:
        user_id = verify_demo_session(demo_session, request.app.state.session_secret)
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid demo session is required",
            )
        return user_id
    if development_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Dev-User-Id is required for local development",
        )
    return development_user_id


CurrentUserId = Annotated[UUID, Depends(current_user_id)]


def ensure_development_user(session: Session, user_id: UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        user = User(id=user_id, external_subject=f"dev:{user_id}")
        session.add(user)
    return user


def authorize_tenant_action(
    session: Session,
    *,
    tenant_id: UUID,
    user_id: UUID,
    action: Action,
) -> Tenant:
    """Resolve tenant and membership without revealing cross-tenant existence."""

    set_tenant_context(session, tenant_id)
    tenant = session.get(Tenant, tenant_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user_id,
        )
    )
    if tenant is None or membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )

    context = AccessContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=frozenset({Role(membership.role)}),
    )
    try:
        authorize(context, ResourceScope(tenant_id=tenant_id), action)
    except PermissionDenied as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Resource not found or access denied",
        ) from error
    return tenant
