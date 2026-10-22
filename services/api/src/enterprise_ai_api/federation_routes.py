"""OIDC authorization-code login and a tenant-scoped SCIM 2.0 Users profile."""

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.auth_routes import (
    DatabaseSession,
    _enforce_login_limit,
    _issue_account_token,
    _new_session,
    _normalize_email,
    _set_session_cookie,
)
from enterprise_ai_api.crypto import blind_index, new_token, token_hash
from enterprise_ai_api.database import set_tenant_context
from enterprise_ai_api.federation import subject_hash, verify_id_token
from enterprise_ai_api.models import (
    AccountToken,
    AuthAttempt,
    AuthSession,
    FederatedIdentity,
    Membership,
    OidcTransaction,
    Tenant,
    User,
)

router = APIRouter()
SCIM_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def _provider(request, key):
    provider = request.app.state.identity_providers.get(key)
    if provider is None or not request.app.state.account_auth:
        raise HTTPException(404, "Identity provider not configured")
    return provider


def _secure(request):
    return request.url.scheme == "https" or request.app.state.app_env != "development"


def _callback(request, provider):
    return f"{request.app.state.public_url}/v1/auth/sso/{provider.key}/callback"


@router.get("/v1/auth/sso/providers")
def providers(request: Request):
    if not request.app.state.account_auth:
        return []
    return [
        {"name": p.display_name, "login_url": f"/v1/auth/sso/{p.key}/login"}
        for p in request.app.state.identity_providers.values()
    ]


@router.get("/v1/auth/sso/{key}/login")
def login(key: str, request: Request, session: DatabaseSession):
    provider = _provider(request, key)
    state, browser, nonce, verifier = new_token(), new_token(), new_token(), new_token()
    network_hash = token_hash(f"sso-network:{request.client.host if request.client else 'unknown'}")
    _enforce_login_limit(session, token_hash(state), network_hash)
    session.add(AuthAttempt(identity_hash=token_hash(state), network_hash=network_hash))
    session.execute(delete(OidcTransaction).where(OidcTransaction.expires_at < datetime.now(UTC)))
    session.add(
        OidcTransaction(
            state_hash=token_hash(state),
            browser_hash=token_hash(browser),
            provider=key,
            nonce=nonce,
            verifier=json.dumps(
                {"value": verifier, "issuer": provider.issuer, "client_id": provider.client_id}
            ),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    session.commit()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    response = RedirectResponse(
        provider.authorization_endpoint
        + "?"
        + urlencode(
            {
                "client_id": provider.client_id,
                "redirect_uri": _callback(request, provider),
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ),
        status_code=302,
    )
    response.set_cookie(
        "enterprise_ai_oidc",
        browser,
        max_age=300,
        httponly=True,
        secure=_secure(request),
        samesite="lax",
        path="/v1/auth/sso",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/v1/auth/sso/{key}/callback")
def callback(key: str, request: Request, session: DatabaseSession, state: str = "", code: str = ""):
    provider = _provider(request, key)
    if len(state) > 256 or not code or len(code) > 4096:
        raise HTTPException(400, "Invalid SSO callback")
    transaction = session.get(OidcTransaction, token_hash(state))
    browser = request.cookies.get("enterprise_ai_oidc", "")
    now = datetime.now(UTC)
    if (
        transaction is None
        or transaction.provider != key
        or not browser
        or not hmac.compare_digest(transaction.browser_hash, token_hash(browser))
    ):
        raise HTTPException(400, "SSO state invalid; restart sign-in")
    verifier = json.loads(transaction.verifier)
    nonce = transaction.nonce
    consumed = session.execute(
        delete(OidcTransaction)
        .execution_options(synchronize_session=False)
        .where(OidcTransaction.state_hash == token_hash(state), OidcTransaction.expires_at > now)
    )
    session.commit()
    if (
        consumed.rowcount != 1
        or verifier["issuer"] != provider.issuer
        or verifier["client_id"] != provider.client_id
    ):
        raise HTTPException(400, "SSO request expired; restart sign-in")
    try:
        transport = request.app.state.identity_transport
        result = transport.request(
            provider.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _callback(request, provider),
                "client_id": provider.client_id,
                "client_secret": os.environ[provider.client_secret_env],
                "code_verifier": verifier["value"],
            },
        )
        claims = verify_id_token(
            result.get("id_token"), provider, nonce, transport.request(provider.jwks_uri)
        )
    except Exception:
        raise HTTPException(401, "SSO verification failed; restart sign-in") from None
    set_tenant_context(session, provider.tenant_id)
    identity = session.scalar(
        select(FederatedIdentity).where(
            FederatedIdentity.tenant_id == provider.tenant_id,
            FederatedIdentity.provider == key,
            FederatedIdentity.issuer == provider.issuer,
            FederatedIdentity.subject_hash == subject_hash(claims["sub"]),
            FederatedIdentity.deleted.is_(False),
        )
    )
    if identity is None:
        raise HTTPException(403, "An administrator must provision this identity through SCIM first")
    user = session.get(User, identity.user_id)
    membership = session.get(Membership, (provider.tenant_id, user.id))
    if (
        user.status != "active"
        or membership is None
        or not membership.active
        or not membership.provisioning_active
        or user.email_lookup_hash != blind_index(_normalize_email(claims["email"]))
    ):
        raise HTTPException(403, "Account unavailable or identity does not match provisioning")
    user.email_verified_at = now
    user.default_tenant_id = provider.tenant_id
    response = RedirectResponse(
        request.app.state.public_url + ("/?mode=sso" if user.mfa_enabled_at else "/"),
        status_code=302,
    )
    if user.mfa_enabled_at:
        response.delete_cookie("enterprise_ai_session", path="/")
        challenge = _issue_account_token(session, user.id, "mfa_login")
        response.set_cookie(
            "enterprise_ai_sso_mfa",
            challenge,
            max_age=300,
            httponly=True,
            secure=_secure(request),
            samesite="lax",
            path="/v1/auth/sso",
        )
    else:
        token = _new_session(session, user.id, request, auth_method="oidc")
        _set_session_cookie(request, response, token)
    record_audit_event(
        session,
        tenant_id=provider.tenant_id,
        actor_id=user.id,
        action="account.sso_verified",
        resource_type="user",
        resource_id=user.id,
        details={"provider": key, "mfa_pending": bool(user.mfa_enabled_at)},
    )
    session.commit()
    response.delete_cookie("enterprise_ai_oidc", path="/v1/auth/sso")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/v1/auth/sso/complete")
def complete(request: Request, response: Response):
    token = request.cookies.get("enterprise_ai_sso_mfa")
    if not token:
        raise HTTPException(401, "Restart enterprise sign-in")
    response.delete_cookie("enterprise_ai_sso_mfa", path="/v1/auth/sso")
    response.headers["Cache-Control"] = "no-store"
    return {"mfa_required": True, "challenge_token": token}


def _scim_error(status, detail, kind=None):
    payload = {"schemas": [SCIM_ERROR], "status": str(status), "detail": detail}
    if kind:
        payload["scimType"] = kind
    return JSONResponse(payload, status_code=status, media_type="application/scim+json")


def _scim_auth(request, key, session):
    provider = _provider(request, key)
    expected = os.getenv(provider.scim_token_env, "")
    supplied = request.headers.get("Authorization", "")
    if len(expected) < 32 or not hmac.compare_digest(
        supplied.encode(), ("Bearer " + expected).encode()
    ):
        raise HTTPException(401, "SCIM bearer token required")
    set_tenant_context(session, provider.tenant_id)
    if session.get(Tenant, provider.tenant_id) is None:
        raise HTTPException(404, "Provisioning tenant unavailable")
    return provider


def _identities(provider):
    return select(FederatedIdentity).where(
        FederatedIdentity.tenant_id == provider.tenant_id,
        FederatedIdentity.provider == provider.key,
        FederatedIdentity.issuer == provider.issuer,
        FederatedIdentity.deleted.is_(False),
    )


def _resource(session, identity):
    user = session.get(User, identity.user_id)
    membership = session.get(Membership, (identity.tenant_id, user.id))
    return {
        "schemas": [SCIM_USER],
        "id": str(identity.id),
        "externalId": identity.subject,
        "userName": user.email_ciphertext,
        "active": membership.provisioning_active,
        "meta": {"resourceType": "User"},
    }


def _audit(session, provider, identity, action):
    record_audit_event(
        session,
        tenant_id=provider.tenant_id,
        actor_id=None,
        action=f"scim.user_{action}",
        resource_type="user",
        resource_id=identity.user_id,
        details={"provider": provider.key},
    )


@router.get("/scim/v2/{key}/ServiceProviderConfig")
def scim_config(key: str, request: Request, session: DatabaseSession):
    _scim_auth(request, key, session)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 100},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {
                "type": "oauthbearertoken",
                "name": "Bearer token",
                "description": "Tenant-scoped provisioning token",
            }
        ],
    }


@router.api_route("/scim/v2/{key}/Users", methods=["GET", "POST"])
async def scim_users(key: str, request: Request, session: DatabaseSession):
    provider = _scim_auth(request, key, session)
    if request.method == "GET":
        try:
            start, count = (
                int(request.query_params.get("startIndex", "1")),
                int(request.query_params.get("count", "100")),
            )
            if start < 1 or count < 0 or count > 100:
                raise ValueError()
        except ValueError:
            return _scim_error(400, "startIndex >= 1 and 0 <= count <= 100 required")
        statement = _identities(provider)
        filter_value = request.query_params.get("filter")
        if filter_value:
            match = re.fullmatch(r'(userName|externalId) eq ("(?:[^"\\]|\\.)*")', filter_value)
            if not match:
                return _scim_error(
                    400, "Supported filters: userName eq or externalId eq", "invalidFilter"
                )
            try:
                value = json.loads(match[2])
            except ValueError:
                return _scim_error(400, "Invalid filter", "invalidFilter")
            if match[1] == "externalId":
                statement = statement.where(FederatedIdentity.subject_hash == subject_hash(value))
            else:
                statement = statement.join(User).where(User.email_lookup_hash == blind_index(value))
        identities = list(session.scalars(statement.order_by(FederatedIdentity.id)))
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(identities),
            "startIndex": start,
            "itemsPerPage": len(identities[start - 1 : start - 1 + count]),
            "Resources": [
                _resource(session, identity)
                for identity in identities[start - 1 : start - 1 + count]
            ],
        }
    payload = await _body(request)
    if isinstance(payload, Response):
        return payload
    try:
        email = _normalize_email(payload.get("userName", ""))
        subject = payload["externalId"]
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
            or type(payload.get("active", True)) is not bool
        ):
            raise ValueError()
        if SCIM_USER not in payload.get("schemas", []) or any(
            field in payload for field in ("password", "roles", "entitlements")
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError, HTTPException, AttributeError):
        return _scim_error(
            400,
            "Email userName and immutable OIDC subject externalId required; "
            "roles/passwords are not provisionable",
            "invalidValue",
        )
    user_id = uuid4()
    user = User(
        id=user_id,
        external_subject=f"scim:{user_id}",
        email_ciphertext=email,
        email_lookup_hash=blind_index(email),
        default_tenant_id=provider.tenant_id,
    )
    identity = FederatedIdentity(
        tenant_id=provider.tenant_id,
        user_id=user_id,
        provider=key,
        issuer=provider.issuer,
        subject=subject,
        subject_hash=subject_hash(subject),
    )
    session.add(user)
    try:
        session.flush()
        session.add(
            Membership(
                tenant_id=provider.tenant_id,
                user_id=user_id,
                role="member",
                provisioning_active=payload.get("active", True),
            )
        )
        session.add(identity)
        session.flush()
        _audit(session, provider, identity, "created")
        result = _resource(session, identity)
        session.commit()
    except IntegrityError:
        session.rollback()
        return _scim_error(
            409,
            "Identity or email already exists; accounts are never auto-linked by email",
            "uniqueness",
        )
    return JSONResponse(
        result,
        status_code=201,
        media_type="application/scim+json",
        headers={"Location": f"{request.app.state.public_url}/scim/v2/{key}/Users/{identity.id}"},
    )


async def _body(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            return _scim_error(413, "Provisioning request too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError()
        return payload
    except ValueError:
        return _scim_error(400, "Expected a JSON object", "invalidSyntax")


@router.api_route("/scim/v2/{key}/Users/{identity_id}", methods=["GET", "PATCH", "PUT", "DELETE"])
async def scim_user(key: str, identity_id: UUID, request: Request, session: DatabaseSession):
    session.autoflush = False
    provider = _scim_auth(request, key, session)
    identity = session.scalar(_identities(provider).where(FederatedIdentity.id == identity_id))
    if identity is None:
        return _scim_error(404, "User not found")
    if request.method == "GET":
        return _resource(session, identity)
    session.execute(
        update(Membership)
        .where(
            Membership.tenant_id == provider.tenant_id,
            Membership.user_id == identity.user_id,
        )
        .values(provisioning_active=Membership.provisioning_active)
    )
    membership = session.get(Membership, (provider.tenant_id, identity.user_id))
    user = session.get(User, identity.user_id)
    if request.method == "DELETE":
        identity.deleted = True
        membership.provisioning_active = False
        # Retain audit/document authorship, but release identifiers for a new resource.
        # Re-provisioning creates a fresh user with no inherited role/space grants.
        identity.subject_hash = subject_hash(f"deleted:{identity.id}")
        user.email_lookup_hash = None
    else:
        payload = await _body(request)
        if isinstance(payload, Response):
            return payload
        changes = {}
        if request.method == "PUT":
            if payload.get(
                "externalId", identity.subject
            ) != identity.subject or SCIM_USER not in payload.get("schemas", []):
                return _scim_error(
                    400, "externalId is immutable and User schema is required", "mutability"
                )
            changes = {
                k: v
                for k, v in payload.items()
                if k not in {"schemas", "id", "meta", "externalId", "name", "displayName", "emails"}
            }
            if "userName" not in changes:
                return _scim_error(400, "userName is required", "invalidValue")
            changes.setdefault("active", True)
        else:
            operations = payload.get("Operations")
            if (
                SCIM_PATCH not in payload.get("schemas", [])
                or not isinstance(operations, list)
                or not 1 <= len(operations) <= 20
            ):
                return _scim_error(400, "Invalid PATCH operations", "invalidSyntax")
            for operation in operations:
                if not isinstance(operation, dict) or str(operation.get("op", "")).lower() not in {
                    "add",
                    "replace",
                }:
                    return _scim_error(
                        400, "Only add/replace of active and userName supported", "invalidPath"
                    )
                path, value = operation.get("path"), operation.get("value")
                if path is None and isinstance(value, dict):
                    changes.update(value)
                elif path in {"active", "userName"}:
                    changes[path] = value
                else:
                    return _scim_error(400, "Unsupported PATCH path", "invalidPath")
        if set(changes) - {"active", "userName"} or (
            "active" in changes and type(changes["active"]) is not bool
        ):
            return _scim_error(
                400, "Only boolean active and email userName are writable", "invalidValue"
            )
        if "userName" in changes:
            try:
                email = _normalize_email(changes["userName"])
            except (HTTPException, AttributeError, TypeError):
                return _scim_error(400, "Invalid userName", "invalidValue")
            if user.email_lookup_hash != blind_index(email):
                user.email_verified_at = None
                user.email_ciphertext, user.email_lookup_hash = email, blind_index(email)
        if "active" in changes:
            membership.provisioning_active = changes["active"]
    # Identity changes invalidate sessions and in-flight MFA/password-reset tokens.
    session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    session.execute(delete(AccountToken).where(AccountToken.user_id == user.id))
    _audit(session, provider, identity, "deleted" if identity.deleted else "updated")
    try:
        result = _resource(session, identity)
        session.commit()
    except IntegrityError:
        session.rollback()
        return _scim_error(409, "Email already exists", "uniqueness")
    return Response(status_code=204) if identity.deleted else result
