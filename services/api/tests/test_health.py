from fastapi.testclient import TestClient

from enterprise_ai_api.main import app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "enterprise-ai-api"}

