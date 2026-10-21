import base64
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from enterprise_ai_api.main import create_app
from enterprise_ai_api.operations import assess_production_posture


def test_readiness_checks_database_and_adds_security_headers(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))

    response = TestClient(app).get("/readyz", headers={"X-Request-ID": "client-request-123"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "enterprise-ai-api",
        "database": "ok",
    }
    assert response.headers["x-request-id"] == "client-request-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_invalid_request_id_is_replaced(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))

    response = TestClient(app).get("/healthz", headers={"X-Request-ID": "unsafe value"})

    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) == 32
    assert response.headers["x-request-id"] != "unsafe value"


def test_readiness_returns_503_when_database_is_unavailable(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))

    class UnavailableEngine:
        def connect(self) -> None:
            raise OperationalError("SELECT 1", {}, RuntimeError("offline"))

    app.state.engine = UnavailableEngine()
    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["database"] == "unavailable"


def test_production_posture_reports_missing_enterprise_controls(
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db/enterprise_ai")
    monkeypatch.setenv("ACCOUNT_AUTH_MODE", "true")
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "configured")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://knowledge.example.com")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.setenv("EMAIL_DELIVERY_BACKEND", "disabled")
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("BACKUP_STORAGE_BACKEND", "local")

    posture = assess_production_posture()
    results = {check.name: check.passed for check in posture.checks}

    assert posture.production_ready is False
    assert results["postgresql_database"] is True
    assert results["persistent_object_storage"] is False
    assert results["verified_email_delivery"] is False
    assert results["encrypted_backup_key"] is False


def test_production_posture_passes_with_all_controls(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:secret@db/enterprise_ai")
    monkeypatch.setenv("ACCOUNT_AUTH_MODE", "true")
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "configured")
    monkeypatch.setenv("APP_PUBLIC_URL", "https://knowledge.example.com")
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "documents-access")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "documents-secret")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "documents")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("EMAIL_DELIVERY_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "security@example.com")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"b" * 32).decode()
    )
    monkeypatch.setenv("BACKUP_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("BACKUP_STORAGE_ACCESS_KEY", "backups-access")
    monkeypatch.setenv("BACKUP_STORAGE_SECRET_KEY", "backups-secret")
    monkeypatch.setenv("BACKUP_STORAGE_BUCKET", "backups")
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "35")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "2555")

    posture = assess_production_posture()

    assert posture.production_ready is True
    assert all(check.passed for check in posture.checks)
