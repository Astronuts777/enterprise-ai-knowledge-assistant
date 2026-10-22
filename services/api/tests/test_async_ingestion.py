import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_documents_api import ADMIN_A, ADMIN_B, create_workspace, headers

from enterprise_ai_api.ai_providers import AIProviderError
from enterprise_ai_api.database import PROJECT_ROOT
from enterprise_ai_api.file_scanning import FileScanUnavailable, UnsafeFile
from enterprise_ai_api.ingestion import process_one
from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentChunk, DocumentVersion, IngestionJob, Membership


@pytest.fixture
def queued(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "async")
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Async tenant")
        response = client.post(
            f"/v1/tenants/{tenant}/spaces/{space}/documents",
            headers=headers(ADMIN_A),
            files={"file": ("policy.txt", b"A durable company policy")},
        )
        assert response.status_code == 201
        version = response.json()["version"]
        assert version["status"] == "queued"
        yield app, client, UUID(tenant), UUID(version["id"])


def work(app, tenant):
    return process_one(
        app.state.session_factory,
        app.state.object_storage,
        app.state.file_scanner,
        app.state.embedding_provider,
        tenant,
    )


def state(app, version):
    with app.state.session_factory() as session:
        job = session.scalar(select(IngestionJob).where(IngestionJob.version_id == version))
        return (
            session.get(DocumentVersion, version).status,
            job.status,
            job.attempts,
            job.error_code,
        )


def test_queue_persists_until_worker_and_requires_publish(queued):
    app, client, tenant, version = queued
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    path = f"/v1/tenants/{tenant}/document-versions/{version}"
    assert client.post(path + "/publish", headers=headers(ADMIN_A)).status_code == 409
    assert client.get(path + "/job", headers=headers(ADMIN_B)).status_code == 404
    assert work(app, tenant)
    assert state(app, version)[:3] == ("ready", "succeeded", 1)
    assert not work(app, tenant)
    assert client.post(path + "/publish", headers=headers(ADMIN_A)).status_code == 200
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 1


@pytest.mark.parametrize(
    "error,expected,retries",
    [
        (UnsafeFile("unsafe"), "unsafe_file", False),
        (FileScanUnavailable("offline"), "scanner_unavailable", True),
    ],
)
def test_scan_failure_is_fail_closed_with_bounded_retry(queued, error, expected, retries):
    app, client, tenant, version = queued

    class Scanner:
        name = "test"

        def scan(self, content):
            raise error

    app.state.file_scanner = Scanner()
    for attempt in range(1, 4 if retries else 2):
        assert work(app, tenant)
        result = state(app, version)
        assert result[3] == expected
        if retries and attempt < 3:
            assert result[:3] == ("queued", "queued", attempt)
            with app.state.session_factory() as session:
                session.scalar(select(IngestionJob)).available_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
                session.commit()
    assert state(app, version)[0] == "failed"
    assert not work(app, tenant)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    response = client.post(
        f"/v1/tenants/{tenant}/document-versions/{version}/job/retry", headers=headers(ADMIN_A)
    )
    assert response.status_code == (200 if retries else 409)


def test_revoked_uploader_cannot_complete_work(queued):
    app, _, tenant, version = queued
    with app.state.session_factory() as session:
        session.get(Membership, (tenant, ADMIN_A)).active = False
        session.commit()
    assert work(app, tenant)
    assert state(app, version) == ("failed", "failed", 1, "access_revoked")


def test_cancel_during_work_fences_late_results_and_retry_succeeds(queued):
    app, client, tenant, version = queued
    original = app.state.file_scanner

    class Scanner:
        name = "test"

        def scan(self, content):
            response = client.post(
                f"/v1/tenants/{tenant}/document-versions/{version}/job/cancel",
                headers=headers(ADMIN_A),
            )
            assert response.status_code == 200

    app.state.file_scanner = Scanner()
    assert work(app, tenant)
    assert state(app, version)[:2] == ("failed", "cancelled")
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    app.state.file_scanner = original
    assert (
        client.post(
            f"/v1/tenants/{tenant}/document-versions/{version}/job/retry", headers=headers(ADMIN_A)
        ).status_code
        == 200
    )
    assert work(app, tenant)
    assert state(app, version)[:2] == ("ready", "succeeded")


def test_expired_worker_lease_reclaimed_but_live_lease_not_stolen(queued):
    app, _, tenant, version = queued
    with app.state.session_factory() as session:
        job = session.scalar(select(IngestionJob))
        job.status, job.attempts, job.lease_token = "running", 1, "crashed-worker"
        job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
        session.commit()
    assert not work(app, tenant)
    with app.state.session_factory() as session:
        session.scalar(select(IngestionJob)).lease_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        session.commit()
    assert work(app, tenant)
    assert state(app, version)[:3] == ("ready", "succeeded", 2)


def test_enabled_embedding_outage_does_not_silently_claim_index_success(queued):
    app, _, tenant, version = queued

    class Embeddings:
        enabled, external, model_name = True, False, "local-test"

        def embed(self, texts):
            raise AIProviderError("offline")

    app.state.embedding_provider = Embeddings()
    assert work(app, tenant)
    assert state(app, version) == ("queued", "queued", 1, "embedding_unavailable")


def test_standalone_worker_process_resumes_persisted_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PROCESSING_MODE", "async")
    database_url = f"sqlite:///{tmp_path / 'durable.db'}"
    storage = tmp_path / "uploads"
    app = create_app(database_url, storage_root=str(storage))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Persisted worker test")
        response = client.post(
            f"/v1/tenants/{tenant}/spaces/{space}/documents",
            headers=headers(ADMIN_A),
            files={"file": ("policy.txt", b"Persisted queue content")},
        )
        assert response.status_code == 201
        version = UUID(response.json()["version"]["id"])
    app.state.engine.dispose()
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "MIGRATION_DATABASE_URL": "",
        "LOCAL_STORAGE_PATH": str(storage),
        "OBJECT_STORAGE_BACKEND": "local",
        "FILE_SCAN_BACKEND": "disabled",
        "FILE_SCAN_REQUIRED": "false",
        "EMBEDDING_PROVIDER": "disabled",
        "APP_ENV": "development",
        "ACCOUNT_AUTH_MODE": "false",
        "WORKER_TENANT_IDS": "",
        "PYTHONPATH": os.pathsep.join(
            str(PROJECT_ROOT / path)
            for path in ("services/api/src", "services/worker/src", "packages/core/src")
        ),
    }
    process = subprocess.run(
        [sys.executable, "-m", "enterprise_ai_worker.main", "--once"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    reopened = create_app(database_url, storage_root=str(storage))
    assert state(reopened, version)[:3] == ("ready", "succeeded", 1)
    reopened.state.engine.dispose()


def test_cancel_prevents_subsequent_model_transfer(queued):
    app, client, tenant, version = queued

    class Embeddings:
        enabled, external, model_name = True, False, "test-model"

        def embed(self, texts):
            pytest.fail("Cancelled jobs must not transfer content to a model")

    class Scanner:
        name = "test"

        def scan(self, content):
            assert (
                client.post(
                    f"/v1/tenants/{tenant}/document-versions/{version}/job/cancel",
                    headers=headers(ADMIN_A),
                ).status_code
                == 200
            )

    app.state.embedding_provider, app.state.file_scanner = Embeddings(), Scanner()
    assert work(app, tenant)
    assert state(app, version)[:2] == ("failed", "cancelled")
