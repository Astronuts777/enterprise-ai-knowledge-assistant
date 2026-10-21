import os
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from enterprise_ai_api.database import (
    Base,
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
)
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


def create_app(database_url: str | None = None) -> FastAPI:
    application = FastAPI(
        title="Enterprise AI Knowledge Assistant API",
        version="0.1.0",
        description="Permission-aware knowledge and decision evidence API.",
    )
    engine = create_database_engine(database_url or database_url_from_environment())
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)
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
    return application


app = create_app()

