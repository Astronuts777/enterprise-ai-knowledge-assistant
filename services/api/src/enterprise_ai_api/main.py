import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["enterprise-ai-api"]


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


def create_app(database_url: str | None = None, storage_root: str | None = None) -> FastAPI:
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
    application.state.storage_root = (
        storage_root_from_environment() if storage_root is None else Path(storage_root).resolve()
    )
    application.state.storage_root.mkdir(parents=True, exist_ok=True)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_environment(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Dev-User-Id"],
    )

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)

    @application.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="enterprise-ai-api")

    application.include_router(router, tags=["tenancy"])
    application.include_router(document_router, tags=["documents"])
    application.include_router(question_router, tags=["knowledge-answers"])
    return application


app = create_app()

