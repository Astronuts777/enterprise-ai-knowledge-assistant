"""Password account, server-side session, and invitation endpoints."""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_ai_api.crypto import (
    blind_index,
    new_token,
    password_hash,
    password_matches,
    token_hash,
)
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import AuthSession, Invitation, Membership, Tenant, User
from enterprise_ai_api.schemas import (
    AccountLogin,
    AccountRegister,
    AccountResponse,
    InvitationAccept,
    InvitationCreate,
    InvitationResponse,
)
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]
SESSION_COOKIE_NAME = "enterprise_ai_session"
SESSION_LIFETIME = timedelta(hours=12)
INVITATION_LIFETIME = timedelta(days=7)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid email address",
        )
    return email


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.url.scheme == "https" or request.app.state.app_env != "development"
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _new_session(session: Session, user_id: UUID) -> str:
    token = new_token()
    session.add(
        AuthSession(
            user_id=user_id,
            token_hash=token_hash(token),
            expires_at=datetime.now(UTC) + SESSION_LIFETIME,
        )
    )
    return token


def _account_response(user: User) -> AccountResponse:
    if user.email_ciphertext is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is incomplete")
    return AccountResponse(
        user_id=user.id,
        email=user.email_ciphertext,
        tenant_id=user.default_tenant_id,
    )


@router.post("/auth/register", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
def register_account(
    payload: AccountRegister,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse:
    if not request.app.state.account_auth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    email = _normalize_email(payload.email)
    email_hash = blind_index(email)
    if session.scalar(select(User.id).where(User.email_lookup_hash == email_hash)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")

    user = User(
        id=uuid4(),
        external_subject=f"password:{email_hash}",
        email_ciphertext=email,
        email_lookup_hash=email_hash,
        password_hash=password_hash(payload.password),
    )
    session.add(user)
    session.flush()

    if payload.organization_name:
        tenant = Tenant(id=uuid4(), name=payload.organization_name.strip())
        set_tenant_context(session, tenant.id)
        session.add(tenant)
        session.flush()
        session.add(
            Membership(tenant_id=tenant.id, user_id=user.id, role="enterprise_admin")
        )
        user.default_tenant_id = tenant.id

    token = _new_session(session, user.id)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        ) from error
    _set_session_cookie(request, response, token)
    return _account_response(user)


@router.post("/auth/login", response_model=AccountResponse)
def login_account(
    payload: AccountLogin,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse:
    if not request.app.state.account_auth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    email_hash = blind_index(_normalize_email(payload.email))
    user = session.scalar(select(User).where(User.email_lookup_hash == email_hash))
    if (
        user is None
        or user.password_hash is None
        or not password_matches(payload.password, user.password_hash)
        or user.status != "active"
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = _new_session(session, user.id)
    session.commit()
    _set_session_cookie(request, response, token)
    return _account_response(user)


@router.get("/auth/me", response_model=AccountResponse)
def current_account(
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> AccountResponse:
    if not request.app.state.account_auth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return _account_response(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_account(request: Request, response: Response, session: DatabaseSession) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth_session = session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        )
        if auth_session is not None:
            session.delete(auth_session)
            session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.post(
    "/tenants/{tenant_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    tenant_id: UUID,
    payload: InvitationCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> InvitationResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_MEMBERS,
    )
    email = _normalize_email(payload.email)
    raw_token = new_token()
    invitation = Invitation(
        tenant_id=tenant_id,
        email_ciphertext=email,
        email_lookup_hash=blind_index(email),
        role=payload.role,
        token_hash=token_hash(raw_token),
        invited_by=user_id,
        expires_at=datetime.now(UTC) + INVITATION_LIFETIME,
    )
    session.add(invitation)
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(invitation)
    return InvitationResponse(
        id=invitation.id,
        tenant_id=tenant_id,
        email=email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invite_token=raw_token,
    )


@router.post("/auth/invitations/{tenant_id}/accept", response_model=AccountResponse)
def accept_invitation(
    tenant_id: UUID,
    payload: InvitationAccept,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> AccountResponse:
    set_tenant_context(session, tenant_id)
    invitation = session.scalar(
        select(Invitation).where(
            Invitation.tenant_id == tenant_id,
            Invitation.token_hash == token_hash(payload.token),
        )
    )
    now = datetime.now(UTC)
    if invitation is None or invitation.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation is invalid")
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")

    user = session.get(User, user_id)
    if user is None or user.email_lookup_hash != invitation.email_lookup_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invitation is for another account",
        )
    membership = session.get(Membership, (tenant_id, user_id))
    if membership is None:
        session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=invitation.role))
    else:
        membership.role = invitation.role
    invitation.accepted_at = now
    user.default_tenant_id = tenant_id
    session.commit()
    return _account_response(user)
