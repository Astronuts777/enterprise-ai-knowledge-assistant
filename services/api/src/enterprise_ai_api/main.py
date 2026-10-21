from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["enterprise-ai-api"]


app = FastAPI(
    title="Enterprise AI Knowledge Assistant API",
    version="0.1.0",
    description="Permission-aware knowledge and decision evidence API.",
)


@app.get("/healthz", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="enterprise-ai-api")

