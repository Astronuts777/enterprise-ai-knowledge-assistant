import argparse
import logging
import os
import time
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from enterprise_ai_api.ai_providers import (
    embedding_provider_from_environment,
    external_ai_allowed_for_restricted_spaces,
)
from enterprise_ai_api.crypto import require_production_key
from enterprise_ai_api.database import (
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
)
from enterprise_ai_api.file_scanning import scanner_from_environment
from enterprise_ai_api.ingestion import process_one
from enterprise_ai_api.models import Tenant
from enterprise_ai_api.object_storage import object_storage_from_environment

logger = logging.getLogger(__name__)


class WorkerResult(StrEnum):
    IDLE = "idle"
    PROCESSED = "processed"


def run_once(factory=None, storage=None, scanner=None, embeddings=None, tenant_ids=()):
    if factory is None:
        return WorkerResult.IDLE
    for tenant_id in tenant_ids:
        if process_one(
            factory,
            storage,
            scanner,
            embeddings,
            tenant_id,
            allow_external_restricted=external_ai_allowed_for_restricted_spaces(),
        ):
            return WorkerResult.PROCESSED
    return WorkerResult.IDLE


def main() -> None:
    parser = argparse.ArgumentParser(description="Durable document processing worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    require_production_key()
    engine = create_database_engine(database_url_from_environment())
    factory = create_session_factory(engine)
    tenants = [
        UUID(value.strip())
        for value in os.getenv("WORKER_TENANT_IDS", "").split(",")
        if value.strip()
    ]
    if engine.dialect.name != "sqlite" and not tenants:
        raise RuntimeError("WORKER_TENANT_IDS is required for PostgreSQL; RLS is never bypassed")
    storage, scanner, embeddings = (
        object_storage_from_environment(),
        scanner_from_environment(),
        embedding_provider_from_environment(),
    )
    try:
        while True:
            if engine.dialect.name == "sqlite":
                with factory() as session:
                    tenants = list(session.scalars(select(Tenant.id)))
            result = run_once(factory, storage, scanner, embeddings, tenants)
            if args.once:
                break
            if result == WorkerResult.IDLE:
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
