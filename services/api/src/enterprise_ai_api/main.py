from typing import Literal

from fastapi import FastAPI
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


def create_app(database_url: str | None = None) -> FastAPI:
    application = FastAPI(
        title="Enterprise AI Knowledge Assistant API",
        version="0.1.0",
        description="Permission-aware knowledge and decision evidence API.",
    )
    engine = create_database_engine(database_url or database_url_from_environment())
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)

    @application.get("/healthz", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="enterprise-ai-api")

    application.include_router(router, tags=["tenancy"])
    return application


app = create_app()

