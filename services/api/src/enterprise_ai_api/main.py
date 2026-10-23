import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Request, Response, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from enterprise_ai_api.ai_providers import (
    EmbeddingProvider,
    EvidenceAnswerProvider,
    LocalEvidenceAnswerProvider,
    answer_provider_from_environment,
    embedding_provider_from_environment,
    external_ai_allowed_for_restricted_spaces,
)
from enterprise_ai_api.audit_routes import router as audit_router
from enterprise_ai_api.auth_routes import router as auth_router
from enterprise_ai_api.crypto import require_production_key
from enterprise_ai_api.database import (
    Base,
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
    prepare_database,
)
from enterprise_ai_api.document_routes import router as document_router
from enterprise_ai_api.email_delivery import EmailSender, email_sender_from_environment
from enterprise_ai_api.federation import IdentityTransport, load_providers
from enterprise_ai_api.federation_routes import _scim_error
from enterprise_ai_api.federation_routes import router as federation_router
from enterprise_ai_api.file_scanning import FileScanner, scanner_from_environment
from enterprise_ai_api.ingestion_routes import router as ingestion_router
from enterprise_ai_api.object_storage import ObjectStorage, object_storage_from_environment
from enterprise_ai_api.question_routes import router as question_router
from enterprise_ai_api.routes import router
from enterprise_ai_api.scim_groups import router as scim_group_router
from enterprise_ai_api.tenancy import (
    SESSION_COOKIE_NAME,
    sign_demo_session,
    verify_demo_session,
)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["enterprise-ai-api"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    service: Literal["enterprise-ai-api"]
    database: Literal["ok", "unavailable"]


class SessionResponse(BaseModel):
    user_id: UUID
    mode: Literal["development", "public_demo"]


OptionalDevelopmentUserId = Annotated[UUID | None, Header(alias="X-Dev-User-Id")]
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
request_logger = logging.getLogger("enterprise_ai_api.requests")


def cors_origins_from_environment() -> list[str]:
    configured = os.getenv("CORS_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]


def create_app(
    database_url: str | None = None,
    storage_root: str | None = None,
    *,
    object_storage: ObjectStorage | None = None,
    email_sender: EmailSender | None = None,
    require_email_verification: bool | None = None,
    public_demo: bool | None = None,
    account_auth: bool | None = None,
    session_secret: str | None = None,
    answer_provider: EvidenceAnswerProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    file_scanner: FileScanner | None = None,
) -> FastAPI:
    application = FastAPI(
        title="Enterprise AI Knowledge Assistant API",
        version="0.1.0",
        description="Permission-aware knowledge and decision evidence API.",
    )
    resolved_database_url = database_url or database_url_from_environment()
    prepare_database(resolved_database_url)
    engine = create_database_engine(resolved_database_url)
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)
    application.state.app_env = os.getenv("APP_ENV", "development").lower()
    application.state.account_auth = (
        os.getenv("ACCOUNT_AUTH_MODE", "").lower() in {"1", "true", "yes"}
        if account_auth is None
        else account_auth
    )
    application.state.public_demo = (
        os.getenv("PUBLIC_DEMO_MODE", "").lower() in {"1", "true", "yes"}
        if public_demo is None
        else public_demo
    )
    application.state.session_secret = session_secret or os.getenv("SESSION_SECRET", "")
    application.state.public_url = os.getenv("APP_PUBLIC_URL", "http://localhost:3000").rstrip("/")
    application.state.identity_providers = load_providers()
    application.state.identity_transport = IdentityTransport()
    if application.state.identity_providers:
        from urllib.parse import urlsplit

        public = urlsplit(application.state.public_url)
        local = (
            application.state.app_env == "development"
            and os.getenv("SSO_ALLOW_LOOPBACK_HTTP") == "true"
            and public.hostname in {"127.0.0.1", "::1"}
        )
        if (
            not application.state.account_auth
            or not public.hostname
            or public.path
            or public.query
            or public.fragment
            or public.username
            or public.password
            or (public.scheme != "https" and not (local and public.scheme == "http"))
        ):
            raise RuntimeError("SSO requires account mode and an HTTPS APP_PUBLIC_URL origin")
    application.state.email_sender = email_sender or email_sender_from_environment()
    application.state.file_scanner = file_scanner or scanner_from_environment()
    processing_mode = os.getenv("DOCUMENT_PROCESSING_MODE", "sync")
    if processing_mode not in {"sync", "async"}:
        raise RuntimeError("DOCUMENT_PROCESSING_MODE must be sync or async")
    application.state.async_ingestion = processing_mode == "async"
    application.state.answer_provider = answer_provider or answer_provider_from_environment()
    application.state.local_answer_provider = LocalEvidenceAnswerProvider()
    application.state.embedding_provider = (
        embedding_provider or embedding_provider_from_environment()
    )
    application.state.external_ai_allowed_for_restricted = (
        external_ai_allowed_for_restricted_spaces()
    )
    application.state.require_email_verification = (
        os.getenv("REQUIRE_EMAIL_VERIFICATION", "false").lower() in {"1", "true", "yes"}
        if require_email_verification is None
        else require_email_verification
    )
    if application.state.account_auth and application.state.public_demo:
        raise RuntimeError("ACCOUNT_AUTH_MODE and PUBLIC_DEMO_MODE cannot both be enabled")
    require_production_key()
    if application.state.public_demo and not application.state.session_secret:
        raise RuntimeError("SESSION_SECRET is required when PUBLIC_DEMO_MODE is enabled")
    if application.state.require_email_verification and not application.state.email_sender.enabled:
        raise RuntimeError("Email delivery must be configured when verification is required")
    local_root = Path(storage_root).resolve() if storage_root is not None else None
    application.state.object_storage = (
        object_storage
        if object_storage is not None
        else object_storage_from_environment(local_root=local_root)
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_environment(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Dev-User-Id", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, error: StarletteHTTPException):
        if request.url.path.startswith("/scim/v2/"):
            return _scim_error(error.status_code, str(error.detail))
        return await http_exception_handler(request, error)

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/scim/v2/"):
            return _scim_error(400, "Invalid SCIM request", "invalidValue")
        return await request_validation_exception_handler(request, error)

    @application.middleware("http")
    async def add_security_and_request_tracking(request: Request, call_next) -> Response:
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_request_id
        if not REQUEST_ID_PATTERN.fullmatch(supplied_request_id):
            request_id = uuid4().hex
        request.state.request_id = request_id
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            request_logger.exception(
                json.dumps(
                    {
                        "event": "http_request_failed",
                        "method": request.method,
                        "path": request.url.path,
                        "request_id": request_id,
                    },
                    sort_keys=True,
                )
            )
            raise
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/scim/v2/"):
            response.headers["Cache-Control"] = "no-store"
            if response.status_code != 204:
                response.headers["Content-Type"] = "application/scim+json"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if application.state.app_env not in {"development", "test", "testing"}:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        request_logger.info(
            json.dumps(
                {
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "event": "http_request_completed",
                    "method": request.method,
                    "path": request.url.path,
                    "request_id": request_id,
                    "status_code": response.status_code,
                },
                sort_keys=True,
            )
        )
        return response

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)

    @application.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="enterprise-ai-api")

    @application.get(
        "/readyz",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["operations"],
    )
    def readiness(request: Request) -> ReadinessResponse | JSONResponse:
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            payload = ReadinessResponse(
                status="unavailable",
                service="enterprise-ai-api",
                database="unavailable",
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=payload.model_dump(),
            )
        return ReadinessResponse(
            status="ready",
            service="enterprise-ai-api",
            database="ok",
        )

    @application.post("/v1/session", response_model=SessionResponse, tags=["identity"])
    def open_session(
        request: Request,
        response: Response,
        development_user_id: OptionalDevelopmentUserId = None,
    ) -> SessionResponse:
        if not request.app.state.public_demo:
            return SessionResponse(
                user_id=development_user_id or uuid4(),
                mode="development",
            )

        existing = verify_demo_session(
            request.cookies.get(SESSION_COOKIE_NAME),
            request.app.state.session_secret,
        )
        user_id = existing or uuid4()
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=sign_demo_session(user_id, request.app.state.session_secret),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return SessionResponse(user_id=user_id, mode="public_demo")

    application.include_router(auth_router, tags=["authentication"])
    application.include_router(federation_router, tags=["enterprise-identity"])
    application.include_router(scim_group_router, tags=["enterprise-identity"])
    from enterprise_ai_api.employee_routes import router as employee_router
    application.include_router(employee_router, tags=["employees"])
    from enterprise_ai_api.approval_routes import router as approval_router
    application.include_router(approval_router, tags=["document-approvals"])
    application.include_router(router, tags=["tenancy"])
    application.include_router(document_router, tags=["documents"])
    application.include_router(ingestion_router, tags=["document-processing"])
    application.include_router(question_router, tags=["knowledge-answers"])
    application.include_router(audit_router, tags=["audit"])

    static_directory = os.getenv("WEB_STATIC_DIR")
    if static_directory:
        static_path = Path(static_directory).resolve()
        if not (static_path / "index.html").is_file():
            raise RuntimeError(f"WEB_STATIC_DIR does not contain index.html: {static_path}")
        application.mount("/", StaticFiles(directory=static_path, html=True), name="web")
    return application


app = create_app()
