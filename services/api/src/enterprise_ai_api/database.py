"""Database engine and request-scoped session helpers."""

import os
from collections.abc import Generator
from uuid import UUID

from fastapi import Request
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def database_url_from_environment() -> str:
    return os.getenv("DATABASE_URL", "sqlite://")


def create_database_engine(database_url: str) -> Engine:
    if database_url == "sqlite://":
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.session_factory() as session:
        yield session


def set_tenant_context(session: Session, tenant_id: UUID) -> None:
    """Bind the current transaction to one tenant when PostgreSQL RLS is active."""

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
