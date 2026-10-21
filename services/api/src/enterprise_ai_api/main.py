import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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
from enterprise_ai_api.document_routes import storage_root_from_environment
from enterprise_ai_api.question_routes import router as question_router
from enterprise_ai_api.routes import router
from enterprise_ai_api.tenancy import (
    SESSION_COOKIE_NAME,
    sign_demo_session,
    verify_demo_session,
)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["enterprise-ai-api"]


class SessionResponse(BaseModel):
    user_id: UUID
    mode: Literal["development", "public_demo"]


OptionalDevelopmentUserId = Annotated[UUID | None, Header(alias="X-Dev-User-Id")]


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
    public_demo: bool | None = None,
    account_auth: bool | None = None,
    session_secret: str | None = None,
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
    if application.state.account_auth and application.state.public_demo:
        raise RuntimeError("ACCOUNT_AUTH_MODE and PUBLIC_DEMO_MODE cannot both be enabled")
    require_production_key()
    if application.state.public_demo and not application.state.session_secret:
        raise RuntimeError("SESSION_SECRET is required when PUBLIC_DEMO_MODE is enabled")
    application.state.storage_root = (
        storage_root_from_environment() if storage_root is None else Path(storage_root).resolve()
    )
    application.state.storage_root.mkdir(parents=True, exist_ok=True)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_environment(),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Dev-User-Id"],
    )

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)

    @application.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="enterprise-ai-api")

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
    application.include_router(router, tags=["tenancy"])
    application.include_router(document_router, tags=["documents"])
    application.include_router(question_router, tags=["knowledge-answers"])

    static_directory = os.getenv("WEB_STATIC_DIR")
    if static_directory:
        static_path = Path(static_directory).resolve()
        if not (static_path / "index.html").is_file():
            raise RuntimeError(f"WEB_STATIC_DIR does not contain index.html: {static_path}")
        application.mount("/", StaticFiles(directory=static_path, html=True), name="web")
    return application


app = create_app()

