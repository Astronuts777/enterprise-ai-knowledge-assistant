from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from enterprise_ai_api.main import create_app

ADMIN_A = UUID("00000000-0000-0000-0000-00000000000a")
ADMIN_B = UUID("00000000-0000-0000-0000-00000000000b")
MEMBER_A = UUID("00000000-0000-0000-0000-000000000001")


def client() -> TestClient:
    return TestClient(create_app("sqlite://"))


def headers(user_id: UUID) -> dict[str, str]:
    return {"X-Dev-User-Id": str(user_id)}


def create_tenant(test_client: TestClient, user_id: UUID, name: str) -> dict[str, str]:
    response = test_client.post("/v1/tenants", json={"name": name}, headers=headers(user_id))
    assert response.status_code == 201
    return response.json()


def test_create_tenant_respects_parent_foreign_keys() -> None:
    with client() as test_client:
        with test_client.app.state.engine.connect() as connection:
            foreign_keys_enabled = connection.scalar(text("PRAGMA foreign_keys"))
        tenant = create_tenant(test_client, ADMIN_A, "Foreign key regression")
        members = test_client.get(
            f"/v1/tenants/{tenant['id']}/members",
            headers=headers(ADMIN_A),
        )

    assert foreign_keys_enabled == 1
    assert members.status_code == 200
    assert members.json()[0]["tenant_id"] == tenant["id"]


def test_admin_can_create_and_list_knowledge_spaces() -> None:
    with client() as test_client:
        tenant = create_tenant(test_client, ADMIN_A, "Acme")

        created = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces",
            json={"name": "Company policies", "classification": "confidential"},
            headers=headers(ADMIN_A),
        )
        listed = test_client.get(
            f"/v1/tenants/{tenant['id']}/spaces", headers=headers(ADMIN_A)
        )

    assert created.status_code == 201
    assert created.json()["tenant_id"] == tenant["id"]
    assert listed.status_code == 200
    assert [space["name"] for space in listed.json()] == ["Company policies"]


def test_member_can_read_but_cannot_manage_spaces() -> None:
    with client() as test_client:
        tenant = create_tenant(test_client, ADMIN_A, "Acme")
        membership = test_client.post(
            f"/v1/tenants/{tenant['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )
        read_result = test_client.get(
            f"/v1/tenants/{tenant['id']}/spaces", headers=headers(MEMBER_A)
        )
        write_result = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces",
            json={"name": "Forbidden space"},
            headers=headers(MEMBER_A),
        )

    assert membership.status_code == 201
    assert read_result.status_code == 200
    assert write_result.status_code == 403
    assert write_result.json() == {"detail": "Resource not found or access denied"}


def test_admin_can_list_members_but_member_cannot() -> None:
    with client() as test_client:
        tenant = create_tenant(test_client, ADMIN_A, "Acme")
        test_client.post(
            f"/v1/tenants/{tenant['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )

        admin_result = test_client.get(
            f"/v1/tenants/{tenant['id']}/members", headers=headers(ADMIN_A)
        )
        member_result = test_client.get(
            f"/v1/tenants/{tenant['id']}/members", headers=headers(MEMBER_A)
        )

    assert admin_result.status_code == 200
    assert {member["user_id"] for member in admin_result.json()} == {
        str(ADMIN_A),
        str(MEMBER_A),
    }
    assert member_result.status_code == 403


def test_local_frontend_origin_is_allowed_by_cors() -> None:
    with client() as test_client:
        response = test_client.options(
            "/v1/tenants",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-dev-user-id",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_admin_of_another_tenant_cannot_discover_or_list_spaces() -> None:
    with client() as test_client:
        tenant_a = create_tenant(test_client, ADMIN_A, "Acme")
        create_tenant(test_client, ADMIN_B, "Globex")

        response = test_client.get(
            f"/v1/tenants/{tenant_a['id']}/spaces", headers=headers(ADMIN_B)
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found or access denied"}
    assert tenant_a["name"] not in response.text


def test_space_name_is_unique_only_within_each_tenant() -> None:
    with client() as test_client:
        tenant_a = create_tenant(test_client, ADMIN_A, "Acme")
        tenant_b = create_tenant(test_client, ADMIN_B, "Globex")
        payload = {"name": "Policies"}

        first = test_client.post(
            f"/v1/tenants/{tenant_a['id']}/spaces",
            json=payload,
            headers=headers(ADMIN_A),
        )
        duplicate = test_client.post(
            f"/v1/tenants/{tenant_a['id']}/spaces",
            json=payload,
            headers=headers(ADMIN_A),
        )
        other_tenant = test_client.post(
            f"/v1/tenants/{tenant_b['id']}/spaces",
            json=payload,
            headers=headers(ADMIN_B),
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert other_tenant.status_code == 201
