"""Password account, server-side session, and invitation endpoints."""

import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.crypto import (
    blind_index,
    new_token,
    password_hash,
    password_matches,
    token_hash,
)
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.email_delivery import EmailDeliveryError, EmailSender
from enterprise_ai_api.mfa import (
    new_recovery_codes,
    new_totp_secret,
    normalize_recovery_code,
    verify_totp,
)
from enterprise_ai_api.models import (
    AccountToken,
    AuthAttempt,
    AuthSession,
    Invitation,
    Membership,
    MfaRecoveryCode,
    Tenant,
    User,
)
from enterprise_ai_api.schemas import (
    AccountLogin,
    AccountRegister,
    AccountResponse,
    AuthSessionResponse,
    EmailAddressRequest,
    GenericMessage,
    InvitationAccept,
    InvitationCreate,
    InvitationResponse,
    MfaChallengeResponse,
    MfaCodeRequest,
    MfaConfirmResponse,
    MfaDisableRequest,
    MfaLoginRequest,
    MfaRecoveryCodesRequest,
    MfaSetupRequest,
    MfaSetupResponse,
    PasswordResetConfirm,
    RevokedSessionsResponse,
    TokenRequest,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    authorize_tenant_action,
    tenant_mfa_required,
)
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]
SESSION_COOKIE_NAME = "enterprise_ai_session"
SESSION_LIFETIME = timedelta(hours=12)
INVITATION_LIFETIME = timedelta(days=7)
VERIFICATION_LIFETIME = timedelta(hours=24)
PASSWORD_RESET_LIFETIME = timedelta(minutes=30)
MFA_CHALLENGE_LIFETIME = timedelta(minutes=5)
LOGIN_WINDOW = timedelta(minutes=15)
MAX_LOGIN_FAILURES = 5
MAX_NETWORK_LOGIN_FAILURES = 50
EMAIL_SEND_COOLDOWN = timedelta(minutes=1)
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


def _client_label(request: Request) -> str:
    """Return a coarse device label without retaining the raw user-agent or IP."""

    user_agent = request.headers.get("user-agent", "")
    if "Edg/" in user_agent:
        browser = "Microsoft Edge"
    elif "Firefox/" in user_agent:
        browser = "Firefox"
    elif "Chrome/" in user_agent or "CriOS/" in user_agent:
        browser = "Chrome"
    elif "Safari/" in user_agent:
        browser = "Safari"
    else:
        browser = "Browser"
    if "Windows" in user_agent:
        platform = "Windows"
    elif "Android" in user_agent:
        platform = "Android"
    elif "iPhone" in user_agent or "iPad" in user_agent:
        platform = "iOS"
    elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
        platform = "macOS"
    elif "Linux" in user_agent:
        platform = "Linux"
    else:
        platform = "Unknown device"
    return f"{browser} · {platform}"


def _new_session(
    session: Session, user_id: UUID, request: Request, *, auth_method="password"
) -> str:
    user = session.get(User, user_id)
    if user.password_hash is None:
        if auth_method != "oidc":
            raise HTTPException(403, "This account requires enterprise sign-in")
        # Serialize federated session creation with SCIM revocation.
        set_tenant_context(session, user.default_tenant_id)
        session.execute(
            update(Membership)
            .where(
                Membership.tenant_id == user.default_tenant_id,
                Membership.user_id == user_id,
            )
            .values(provisioning_active=Membership.provisioning_active)
        )
        membership = session.scalar(
            select(Membership)
            .where(
                Membership.tenant_id == user.default_tenant_id,
                Membership.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        if membership is None or not membership.active or not membership.provisioning_active:
            raise HTTPException(403, "Federated account is suspended")
    token = new_token()
    session.add(
        AuthSession(
            user_id=user_id,
            token_hash=token_hash(token),
            client_label=_client_label(request),
            auth_method=auth_method,
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
        email_verified=user.email_verified_at is not None,
        mfa_enabled=user.mfa_enabled_at is not None,
        password_login_enabled=user.password_hash is not None,
    )


def _issue_account_token(session: Session, user_id: UUID, purpose: str) -> str:
    session.execute(
        delete(AccountToken).where(
            AccountToken.user_id == user_id,
            AccountToken.purpose == purpose,
            AccountToken.consumed_at.is_(None),
        )
    )
    raw_token = new_token()
    lifetimes = {
        "email_verification": VERIFICATION_LIFETIME,
        "password_reset": PASSWORD_RESET_LIFETIME,
        "mfa_login": MFA_CHALLENGE_LIFETIME,
    }
    lifetime = lifetimes[purpose]
    session.add(
        AccountToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash(raw_token),
            expires_at=datetime.now(UTC) + lifetime,
        )
    )
    return raw_token


def _valid_account_token(session: Session, raw_token: str, purpose: str) -> AccountToken:
    account_token = session.scalar(
        select(AccountToken)
        .where(
            AccountToken.token_hash == token_hash(raw_token),
            AccountToken.purpose == purpose,
            AccountToken.consumed_at.is_(None),
        )
        .with_for_update()
    )
    if account_token is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token is invalid")
    expires_at = account_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Token has expired")
    return account_token


def _recovery_code_hash(code: str) -> str:
    return token_hash(f"mfa-recovery:{normalize_recovery_code(code)}")


def _verify_user_mfa_code(
    session: Session, user: User, code: str
) -> tuple[str, int | None, MfaRecoveryCode | None] | None:
    if user.mfa_secret_ciphertext is not None:
        counter = verify_totp(
            user.mfa_secret_ciphertext,
            code,
            last_counter=user.mfa_last_counter,
        )
        if counter is not None:
            return "totp", counter, None
    recovery_code = session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.code_hash == _recovery_code_hash(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if recovery_code is not None:
        return "recovery_code", None, recovery_code
    return None


def _send_verification_email(
    sender: EmailSender, *, recipient: str, public_url: str, raw_token: str
) -> None:
    link = f"{public_url}/?{urlencode({'mode': 'verify', 'token': raw_token})}"
    sender.send(
        recipient=recipient,
        subject="验证你的 Enterprise AI 邮箱",
        text=(
            f"请在 24 小时内打开以下链接完成邮箱验证：\n\n{link}\n\n"
            "如果不是你本人操作，请忽略此邮件。"
        ),
    )


def _send_password_reset_email(
    sender: EmailSender, *, recipient: str, public_url: str, raw_token: str
) -> None:
    link = f"{public_url}/?{urlencode({'mode': 'reset', 'token': raw_token})}"
    sender.send(
        recipient=recipient,
        subject="重置你的 Enterprise AI 密码",
        text=(
            f"请在 30 分钟内打开以下链接重置密码：\n\n{link}\n\n如果不是你本人操作，请忽略此邮件。"
        ),
    )


def _login_hashes(request: Request, email_hash: str) -> tuple[str, str]:
    host = request.client.host if request.client else "unknown"
    return email_hash, token_hash(f"login-network:{host}")


def _enforce_login_limit(session: Session, identity_hash: str, network_hash: str) -> None:
    cutoff = datetime.now(UTC) - LOGIN_WINDOW
    identity_failures = session.scalar(
        select(func.count(AuthAttempt.id)).where(
            AuthAttempt.attempted_at >= cutoff,
            AuthAttempt.identity_hash == identity_hash,
        )
    )
    network_failures = session.scalar(
        select(func.count(AuthAttempt.id)).where(
            AuthAttempt.attempted_at >= cutoff,
            AuthAttempt.network_hash == network_hash,
        )
    )
    if (identity_failures or 0) >= MAX_LOGIN_FAILURES or (
        network_failures or 0
    ) >= MAX_NETWORK_LOGIN_FAILURES:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later",
            headers={"Retry-After": str(int(LOGIN_WINDOW.total_seconds()))},
        )


def _recent_token_exists(session: Session, user_id: UUID, purpose: str) -> bool:
    cutoff = datetime.now(UTC) - EMAIL_SEND_COOLDOWN
    return (
        session.scalar(
            select(AccountToken.id).where(
                AccountToken.user_id == user_id,
                AccountToken.purpose == purpose,
                AccountToken.created_at >= cutoff,
                AccountToken.consumed_at.is_(None),
            )
        )
        is not None
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
        email_verified_at=(
            None if request.app.state.require_email_verification else datetime.now(UTC)
        ),
    )
    session.add(user)
    session.flush()

    tenant: Tenant | None = None
    if payload.organization_name:
        tenant = Tenant(id=uuid4(), name=payload.organization_name.strip())
        set_tenant_context(session, tenant.id)
        session.add(tenant)
        session.flush()
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="enterprise_admin"))
        user.default_tenant_id = tenant.id

    if tenant is not None:
        record_audit_event(
            session,
            tenant_id=tenant.id,
            actor_id=user.id,
            action="account.registered",
            resource_type="user",
            resource_id=user.id,
            details={"created_tenant": True},
        )

    verification_token = (
        _issue_account_token(session, user.id, "email_verification")
        if request.app.state.require_email_verification
        else None
    )
    session_token = None if verification_token else _new_session(session, user.id, request)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already exists",
        ) from error
    if verification_token:
        try:
            _send_verification_email(
                request.app.state.email_sender,
                recipient=email,
                public_url=request.app.state.public_url,
                raw_token=verification_token,
            )
        except EmailDeliveryError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Account created, but verification email delivery failed. Try resend",
            ) from error
    elif session_token:
        _set_session_cookie(request, response, session_token)
    return _account_response(user)


@router.post("/auth/login", response_model=AccountResponse | MfaChallengeResponse)
def login_account(
    payload: AccountLogin,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse | MfaChallengeResponse:
    if not request.app.state.account_auth:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    email_hash = blind_index(_normalize_email(payload.email))
    identity_hash, network_hash = _login_hashes(request, email_hash)
    _enforce_login_limit(session, identity_hash, network_hash)
    user = session.scalar(select(User).where(User.email_lookup_hash == email_hash))
    if (
        user is None
        or user.password_hash is None
        or not password_matches(payload.password, user.password_hash)
        or user.status != "active"
    ):
        session.add(AuthAttempt(identity_hash=identity_hash, network_hash=network_hash))
        if user is not None and user.default_tenant_id is not None:
            set_tenant_context(session, user.default_tenant_id)
            record_audit_event(
                session,
                tenant_id=user.default_tenant_id,
                actor_id=user.id,
                action="account.login",
                resource_type="user",
                resource_id=user.id,
                outcome="denied",
                details={"reason": "invalid_credentials"},
            )
        session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if request.app.state.require_email_verification and user.email_verified_at is None:
        if user.default_tenant_id is not None:
            set_tenant_context(session, user.default_tenant_id)
            record_audit_event(
                session,
                tenant_id=user.default_tenant_id,
                actor_id=user.id,
                action="account.login",
                resource_type="user",
                resource_id=user.id,
                outcome="denied",
                details={"reason": "email_unverified"},
            )
            session.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification required",
        )
    session.execute(delete(AuthAttempt).where(AuthAttempt.identity_hash == identity_hash))
    if user.mfa_enabled_at is not None and user.mfa_secret_ciphertext is not None:
        challenge_token = _issue_account_token(session, user.id, "mfa_login")
        session.commit()
        return MfaChallengeResponse(challenge_token=challenge_token)
    token = _new_session(session, user.id, request)
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.login",
            resource_type="user",
            resource_id=user.id,
        )
    session.commit()
    _set_session_cookie(request, response, token)
    return _account_response(user)


@router.post("/auth/mfa/login", response_model=AccountResponse)
def complete_mfa_login(
    payload: MfaLoginRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse:
    challenge = _valid_account_token(session, payload.challenge_token, "mfa_login")
    user = session.get(User, challenge.user_id)
    if (
        user is None
        or user.status != "active"
        or user.mfa_enabled_at is None
        or user.mfa_secret_ciphertext is None
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid challenge")
    verified = _verify_user_mfa_code(session, user, payload.code)
    if verified is None:
        challenge.attempt_count += 1
        if challenge.attempt_count >= 5:
            challenge.consumed_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )

    method, counter, recovery_code = verified
    now = datetime.now(UTC)
    challenge.consumed_at = now
    if counter is not None:
        user.mfa_last_counter = counter
    if recovery_code is not None:
        recovery_code.used_at = now
    session_token = _new_session(
        session, user.id, request, auth_method="oidc" if user.password_hash is None else "password"
    )
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.login",
            resource_type="user",
            resource_id=user.id,
            details={"mfa": True, "method": method},
        )
    session.commit()
    _set_session_cookie(request, response, session_token)
    return _account_response(user)


def _confirm_security_identity(session: Session, request: Request, user: User, password: str):
    if user.password_hash is not None:
        if not password_matches(password, user.password_hash):
            raise HTTPException(403, "Password is incorrect")
        return
    stored = session.scalar(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.token_hash == token_hash(request.cookies.get(SESSION_COOKIE_NAME, "")),
            AuthSession.auth_method == "oidc",
            AuthSession.created_at > datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    if stored is None:
        raise HTTPException(403, "Sign in through SSO again before changing MFA")


@router.post("/auth/mfa/setup", response_model=MfaSetupResponse)
def start_mfa_setup(
    payload: MfaSetupRequest,
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> MfaSetupResponse:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    _confirm_security_identity(session, request, user, payload.password)
    if user.mfa_enabled_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is already enabled")
    if user.email_ciphertext is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is incomplete")

    secret = new_totp_secret()
    user.mfa_secret_ciphertext = secret
    user.mfa_last_counter = None
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.mfa_setup_started",
            resource_type="user",
            resource_id=user.id,
        )
    session.commit()
    label = quote(f"Enterprise AI:{user.email_ciphertext}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": "Enterprise AI",
            "algorithm": "SHA1",
            "digits": "6",
            "period": "30",
        }
    )
    return MfaSetupResponse(secret=secret, otpauth_uri=f"otpauth://totp/{label}?{query}")


@router.post("/auth/mfa/confirm", response_model=MfaConfirmResponse)
def confirm_mfa_setup(
    payload: MfaCodeRequest,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> MfaConfirmResponse:
    user = session.get(User, user_id)
    if user is None or user.mfa_secret_ciphertext is None or user.mfa_enabled_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No pending MFA setup")
    counter = verify_totp(user.mfa_secret_ciphertext, payload.code)
    if counter is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )
    now = datetime.now(UTC)
    user.mfa_enabled_at = now
    user.mfa_last_counter = counter
    recovery_codes = new_recovery_codes()
    session.add_all(
        [
            MfaRecoveryCode(user_id=user.id, code_hash=_recovery_code_hash(code))
            for code in recovery_codes
        ]
    )
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.mfa_enabled",
            resource_type="user",
            resource_id=user.id,
        )
    session.commit()
    return MfaConfirmResponse(recovery_codes=recovery_codes)


@router.post("/auth/mfa/recovery-codes", response_model=MfaConfirmResponse)
def regenerate_mfa_recovery_codes(
    payload: MfaRecoveryCodesRequest,
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> MfaConfirmResponse:
    user = session.get(User, user_id)
    if user is None or user.mfa_enabled_at is None or user.mfa_secret_ciphertext is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is not enabled")
    _confirm_security_identity(session, request, user, payload.password)
    verified = _verify_user_mfa_code(session, user, payload.code)
    if verified is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )
    method, counter, recovery_code = verified
    if counter is not None:
        user.mfa_last_counter = counter
    if recovery_code is not None:
        recovery_code.used_at = datetime.now(UTC)
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    recovery_codes = new_recovery_codes()
    session.add_all(
        [
            MfaRecoveryCode(user_id=user.id, code_hash=_recovery_code_hash(code))
            for code in recovery_codes
        ]
    )
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.mfa_recovery_codes_regenerated",
            resource_type="user",
            resource_id=user.id,
            details={"method": method},
        )
    session.commit()
    return MfaConfirmResponse(recovery_codes=recovery_codes)


@router.delete("/auth/mfa", response_model=AccountResponse)
def disable_mfa(
    payload: MfaDisableRequest,
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> AccountResponse:
    user = session.get(User, user_id)
    if user is None or user.mfa_enabled_at is None or user.mfa_secret_ciphertext is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is not enabled")
    _confirm_security_identity(session, request, user, payload.password)
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        tenant = session.get(Tenant, user.default_tenant_id)
        membership = session.get(Membership, (user.default_tenant_id, user.id))
        if (
            tenant is not None
            and membership is not None
            and tenant_mfa_required(tenant, membership)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="MFA is required by the enterprise security policy",
            )
    verified = _verify_user_mfa_code(session, user, payload.code)
    if verified is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid verification code",
        )
    _, _, recovery_code = verified
    if recovery_code is not None:
        recovery_code.used_at = datetime.now(UTC)
    user.mfa_secret_ciphertext = None
    user.mfa_enabled_at = None
    user.mfa_last_counter = None
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    current_hash = token_hash(request.cookies.get(SESSION_COOKIE_NAME, ""))
    session.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.token_hash != current_hash,
        )
    )
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.mfa_disabled",
            resource_type="user",
            resource_id=user.id,
            details={"other_sessions_revoked": True},
        )
    session.commit()
    return _account_response(user)


@router.post("/auth/email/verify", response_model=AccountResponse)
def verify_email(
    payload: TokenRequest,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse:
    account_token = _valid_account_token(session, payload.token, "email_verification")
    user = session.get(User, account_token.user_id)
    if user is None or user.status != "active" or user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token is invalid")
    now = datetime.now(UTC)
    user.email_verified_at = now
    account_token.consumed_at = now
    session_token = _new_session(session, user.id, request)
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.email_verified",
            resource_type="user",
            resource_id=user.id,
        )
    session.commit()
    _set_session_cookie(request, response, session_token)
    return _account_response(user)


@router.post("/auth/email/resend", response_model=GenericMessage)
def resend_verification(
    payload: EmailAddressRequest,
    request: Request,
    session: DatabaseSession,
) -> GenericMessage:
    if not request.app.state.email_sender.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not configured",
        )
    email = _normalize_email(payload.email)
    user = session.scalar(select(User).where(User.email_lookup_hash == blind_index(email)))
    if (
        user is not None
        and user.password_hash is not None
        and user.email_verified_at is None
        and request.app.state.email_sender.enabled
        and not _recent_token_exists(session, user.id, "email_verification")
    ):
        raw_token = _issue_account_token(session, user.id, "email_verification")
        if user.default_tenant_id is not None:
            set_tenant_context(session, user.default_tenant_id)
            record_audit_event(
                session,
                tenant_id=user.default_tenant_id,
                actor_id=user.id,
                action="account.verification_requested",
                resource_type="user",
                resource_id=user.id,
            )
        session.commit()
        with suppress(EmailDeliveryError):
            _send_verification_email(
                request.app.state.email_sender,
                recipient=email,
                public_url=request.app.state.public_url,
                raw_token=raw_token,
            )
    return GenericMessage(message="If the account requires verification, an email was sent")


@router.post("/auth/password/forgot", response_model=GenericMessage)
def forgot_password(
    payload: EmailAddressRequest,
    request: Request,
    session: DatabaseSession,
) -> GenericMessage:
    if not request.app.state.email_sender.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is not configured",
        )
    email = _normalize_email(payload.email)
    user = session.scalar(select(User).where(User.email_lookup_hash == blind_index(email)))
    if (
        user is not None
        and user.status == "active"
        and request.app.state.email_sender.enabled
        and user.password_hash is not None
        and not _recent_token_exists(session, user.id, "password_reset")
    ):
        raw_token = _issue_account_token(session, user.id, "password_reset")
        if user.default_tenant_id is not None:
            set_tenant_context(session, user.default_tenant_id)
            record_audit_event(
                session,
                tenant_id=user.default_tenant_id,
                actor_id=user.id,
                action="account.password_reset_requested",
                resource_type="user",
                resource_id=user.id,
            )
        session.commit()
        with suppress(EmailDeliveryError):
            _send_password_reset_email(
                request.app.state.email_sender,
                recipient=email,
                public_url=request.app.state.public_url,
                raw_token=raw_token,
            )
    return GenericMessage(message="If the account exists, a reset email was sent")


@router.post("/auth/password/reset", response_model=AccountResponse | MfaChallengeResponse)
def reset_password(
    payload: PasswordResetConfirm,
    request: Request,
    response: Response,
    session: DatabaseSession,
) -> AccountResponse | MfaChallengeResponse:
    account_token = _valid_account_token(session, payload.token, "password_reset")
    user = session.get(User, account_token.user_id)
    if user is None or user.status != "active" or user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token is invalid")
    now = datetime.now(UTC)
    user.password_hash = password_hash(payload.password)
    user.email_verified_at = user.email_verified_at or now
    account_token.consumed_at = now
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    mfa_challenge = (
        _issue_account_token(session, user.id, "mfa_login")
        if user.mfa_enabled_at is not None and user.mfa_secret_ciphertext is not None
        else None
    )
    session_token = None if mfa_challenge else _new_session(session, user.id, request)
    if user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user.id,
            action="account.password_reset",
            resource_type="user",
            resource_id=user.id,
        )
    session.commit()
    if mfa_challenge:
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return MfaChallengeResponse(challenge_token=mfa_challenge)
    if session_token:
        _set_session_cookie(request, response, session_token)
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


@router.get("/auth/sessions", response_model=list[AuthSessionResponse])
def list_account_sessions(
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> list[AuthSessionResponse]:
    current_hash = token_hash(request.cookies.get(SESSION_COOKIE_NAME, ""))
    active_sessions = list(
        session.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.expires_at > datetime.now(UTC),
            )
            .order_by(AuthSession.created_at.desc(), AuthSession.id.desc())
        )
    )
    return [
        AuthSessionResponse(
            id=auth_session.id,
            client_label=auth_session.client_label,
            is_current=auth_session.token_hash == current_hash,
            created_at=auth_session.created_at,
            expires_at=auth_session.expires_at,
        )
        for auth_session in active_sessions
    ]


@router.delete("/auth/sessions", response_model=RevokedSessionsResponse)
def revoke_other_account_sessions(
    request: Request,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> RevokedSessionsResponse:
    current_hash = token_hash(request.cookies.get(SESSION_COOKIE_NAME, ""))
    other_sessions = list(
        session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.token_hash != current_hash,
            )
        )
    )
    for auth_session in other_sessions:
        session.delete(auth_session)
    user = session.get(User, user_id)
    if user is not None and user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user_id,
            action="account.sessions_revoked",
            resource_type="user",
            resource_id=user_id,
            details={"scope": "other_sessions", "revoked_count": len(other_sessions)},
        )
    session.commit()
    return RevokedSessionsResponse(revoked_count=len(other_sessions))


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_account_session(
    session_id: UUID,
    request: Request,
    response: Response,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> None:
    auth_session = session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    current_hash = token_hash(request.cookies.get(SESSION_COOKIE_NAME, ""))
    is_current = auth_session.token_hash == current_hash
    session.delete(auth_session)
    user = session.get(User, user_id)
    if user is not None and user.default_tenant_id is not None:
        set_tenant_context(session, user.default_tenant_id)
        record_audit_event(
            session,
            tenant_id=user.default_tenant_id,
            actor_id=user_id,
            action="account.session_revoked",
            resource_type="auth_session",
            resource_id=session_id,
            details={"current_session": is_current},
        )
    session.commit()
    if is_current:
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_account(request: Request, response: Response, session: DatabaseSession) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        auth_session = session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash(token))
        )
        if auth_session is not None:
            user = session.get(User, auth_session.user_id)
            session.delete(auth_session)
            if user is not None and user.default_tenant_id is not None:
                set_tenant_context(session, user.default_tenant_id)
                record_audit_event(
                    session,
                    tenant_id=user.default_tenant_id,
                    actor_id=user.id,
                    action="account.logout",
                    resource_type="user",
                    resource_id=user.id,
                )
            session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.delete(
    "/tenants/{tenant_id}/members/{target_user_id}/sessions",
    response_model=RevokedSessionsResponse,
)
def revoke_member_sessions(
    tenant_id: UUID,
    target_user_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
) -> RevokedSessionsResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.MANAGE_MEMBERS,
    )
    membership = session.get(Membership, (tenant_id, target_user_id))
    target_user = session.get(User, target_user_id)
    if membership is None or target_user is None or target_user.default_tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found or access denied",
        )
    target_sessions = list(
        session.scalars(select(AuthSession).where(AuthSession.user_id == target_user_id))
    )
    for auth_session in target_sessions:
        session.delete(auth_session)
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="account.sessions_revoked_by_admin",
        resource_type="user",
        resource_id=target_user_id,
        details={"revoked_count": len(target_sessions)},
    )
    session.commit()
    return RevokedSessionsResponse(revoked_count=len(target_sessions))


@router.post(
    "/tenants/{tenant_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    tenant_id: UUID,
    payload: InvitationCreate,
    request: Request,
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
        id=uuid4(),
        tenant_id=tenant_id,
        email_ciphertext=email,
        email_lookup_hash=blind_index(email),
        role=payload.role,
        token_hash=token_hash(raw_token),
        invited_by=user_id,
        expires_at=datetime.now(UTC) + INVITATION_LIFETIME,
    )
    session.add(invitation)
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="invitation.created",
        resource_type="invitation",
        resource_id=invitation.id,
        details={"role": payload.role},
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(invitation)
    delivery_status = "manual"
    if request.app.state.email_sender.enabled:
        query = urlencode({"tenant": str(tenant_id), "invite": raw_token})
        link = f"{request.app.state.public_url}/?{query}"
        try:
            request.app.state.email_sender.send(
                recipient=email,
                subject="你已受邀加入 Enterprise AI 企业工作区",
                text=f"管理员邀请你加入企业知识工作区。请在 7 天内打开：\n\n{link}",
            )
            delivery_status = "sent"
        except EmailDeliveryError:
            delivery_status = "manual"
    return InvitationResponse(
        id=invitation.id,
        tenant_id=tenant_id,
        email=email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invite_token=raw_token,
        delivery_status=delivery_status,
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
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify the invited email before accepting the invitation",
        )
    membership = session.get(Membership, (tenant_id, user_id))
    if membership is None:
        session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=invitation.role))
    else:
        if not membership.active:
            raise HTTPException(
                status_code=409, detail="Suspended membership requires admin restoration"
            )
        membership.role = invitation.role
    invitation.accepted_at = now
    user.default_tenant_id = tenant_id
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="invitation.accepted",
        resource_type="invitation",
        resource_id=invitation.id,
        details={"role": invitation.role},
    )
    session.commit()
    return _account_response(user)
