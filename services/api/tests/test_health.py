from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from enterprise_ai_api.main import app, create_app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "enterprise-ai-api"}


def test_public_demo_uses_signed_browser_session_instead_of_identity_header() -> None:
    demo_app = create_app("sqlite://", public_demo=True, session_secret="test-secret")
    claimed_user = UUID("00000000-0000-0000-0000-00000000000a")

    with TestClient(demo_app, base_url="https://testserver") as client:
        unauthenticated = client.post(
            "/v1/tenants",
            json={"name": "Blocked"},
            headers={"X-Dev-User-Id": str(claimed_user)},
        )
        session = client.post(
            "/v1/session",
            headers={"X-Dev-User-Id": str(claimed_user)},
        )
        created = client.post(
            "/v1/tenants",
            json={"name": "Demo tenant"},
            headers={"X-Dev-User-Id": str(claimed_user)},
        )

    assert unauthenticated.status_code == 401
    assert session.status_code == 200
    assert session.json()["mode"] == "public_demo"
    assert session.json()["user_id"] != str(claimed_user)
    assert created.status_code == 201


def test_api_can_serve_exported_web_application(monkeypatch, tmp_path: Path) -> None:
    static_directory = tmp_path / "web"
    static_directory.mkdir()
    (static_directory / "index.html").write_text("<h1>Enterprise AI</h1>", encoding="utf-8")
    monkeypatch.setenv("WEB_STATIC_DIR", str(static_directory))

    static_app = create_app("sqlite://")
    response = TestClient(static_app).get("/")

    assert response.status_code == 200
    assert "Enterprise AI" in response.text

