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


def test_space_acl_hides_ungranted_spaces_and_distinguishes_viewer_from_manager() -> None:
    with client() as test_client:
        tenant = create_tenant(test_client, ADMIN_A, "Acme")
        created = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces",
            json={"name": "Restricted", "classification": "restricted"},
            headers=headers(ADMIN_A),
        ).json()
        test_client.post(
            f"/v1/tenants/{tenant['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "knowledge_admin"},
            headers=headers(ADMIN_A),
        )

        hidden = test_client.get(
            f"/v1/tenants/{tenant['id']}/spaces", headers=headers(MEMBER_A)
        )
        viewer_grant = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces/{created['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "viewer"},
            headers=headers(ADMIN_A),
        )
        visible = test_client.get(
            f"/v1/tenants/{tenant['id']}/spaces", headers=headers(MEMBER_A)
        )
        viewer_upload = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces/{created['id']}/documents",
            files={"file": ("policy.txt", b"restricted policy")},
            headers=headers(MEMBER_A),
        )
        manager_grant = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces/{created['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "manager"},
            headers=headers(ADMIN_A),
        )
        manager_upload = test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces/{created['id']}/documents",
            files={"file": ("policy.txt", b"restricted policy")},
            headers=headers(MEMBER_A),
        )
        revoked = test_client.delete(
            f"/v1/tenants/{tenant['id']}/spaces/{created['id']}/members/{MEMBER_A}",
            headers=headers(ADMIN_A),
        )
        hidden_again = test_client.get(
            f"/v1/tenants/{tenant['id']}/spaces", headers=headers(MEMBER_A)
        )

    assert hidden.json() == []
    assert viewer_grant.status_code == 200
    assert [space["id"] for space in visible.json()] == [created["id"]]
    assert viewer_upload.status_code == 404
    assert manager_grant.json()["role"] == "manager"
    assert manager_upload.status_code == 201
    assert revoked.status_code == 204
    assert hidden_again.json() == []


def test_enterprise_admin_can_review_audit_events_but_member_cannot() -> None:
    with client() as test_client:
        tenant = create_tenant(test_client, ADMIN_A, "Audited Acme")
        test_client.post(
            f"/v1/tenants/{tenant['id']}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )
        test_client.post(
            f"/v1/tenants/{tenant['id']}/spaces",
            json={"name": "Audited space", "classification": "confidential"},
            headers=headers(ADMIN_A),
        )
        denied = test_client.get(
            f"/v1/tenants/{tenant['id']}/members",
            headers=headers(MEMBER_A),
        )
        member_audit = test_client.get(
            f"/v1/tenants/{tenant['id']}/audit-events",
            headers=headers(MEMBER_A),
        )
        admin_audit = test_client.get(
            f"/v1/tenants/{tenant['id']}/audit-events",
            headers=headers(ADMIN_A),
        )

    assert denied.status_code == 403
    assert member_audit.status_code == 403
    assert admin_audit.status_code == 200
    events = admin_audit.json()
    actions = {event["action"] for event in events}
    assert {"tenant.created", "membership.saved", "space.created"}.issubset(actions)
    denied_events = [event for event in events if event["outcome"] == "denied"]
    assert denied_events
    assert all("Audited Acme" not in str(event["details"]) for event in events)
