import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from test_documents_api import ADMIN_A, ADMIN_B, MEMBER_A, create_workspace, headers

from enterprise_ai_api.database import PROJECT_ROOT
from enterprise_ai_api.main import create_app


def test_suspension_blocks_tenant_space_documents_questions_and_restores_grants(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Tenant A")
        other, _ = create_workspace(client, ADMIN_B, "Tenant B")
        for tenant_id, admin in ((tenant, ADMIN_A), (other, ADMIN_B)):
            client.post(
                f"/v1/tenants/{tenant_id}/members",
                headers=headers(admin),
                json={"user_id": str(MEMBER_A), "role": "member"},
            )
        client.post(
            f"/v1/tenants/{tenant}/spaces/{space}/members",
            headers=headers(ADMIN_A),
            json={"user_id": str(MEMBER_A), "role": "viewer"},
        )
        member_path = f"/v1/tenants/{tenant}/members/{MEMBER_A}/status"
        assert (
            client.patch(member_path, json={"active": False}, headers=headers(MEMBER_A)).status_code
            == 403
        )
        assert (
            client.patch(member_path, json={"active": False}, headers=headers(ADMIN_B)).status_code
            == 404
        )
        response = client.patch(member_path, json={"active": False}, headers=headers(ADMIN_A))
        assert response.status_code == 200 and response.json()["active"] is False
        for path in (
            f"/v1/tenants/{tenant}",
            f"/v1/tenants/{tenant}/spaces",
            f"/v1/tenants/{tenant}/spaces/{space}/documents",
            f"/v1/tenants/{tenant}/knowledge-status",
        ):
            assert client.get(path, headers=headers(MEMBER_A)).status_code == 404
        assert (
            client.post(
                f"/v1/tenants/{tenant}/questions",
                json={"question": "policy?"},
                headers=headers(MEMBER_A),
            ).status_code
            == 404
        )
        # Suspending tenant membership must not globally disable a multi-tenant user.
        assert (
            client.get(f"/v1/tenants/{other}/spaces", headers=headers(MEMBER_A)).status_code == 200
        )
        assert (
            client.post(
                f"/v1/tenants/{tenant}/members",
                headers=headers(ADMIN_A),
                json={"user_id": str(MEMBER_A), "role": "member"},
            ).status_code
            == 409
        )
        assert (
            client.patch(member_path, json={"active": True}, headers=headers(ADMIN_A)).json()[
                "active"
            ]
            is True
        )
        spaces = client.get(f"/v1/tenants/{tenant}/spaces", headers=headers(MEMBER_A))
        assert [item["id"] for item in spaces.json()] == [space]
        audit = client.get(f"/v1/tenants/{tenant}/audit-events", headers=headers(ADMIN_A)).text
        assert "membership.suspended" in audit and "membership.restored" in audit


def test_self_suspension_blocked_and_suspended_admin_cannot_change_others(tmp_path):
    app = create_app("sqlite://", storage_root=str(tmp_path))
    with TestClient(app) as client:
        tenant, _ = create_workspace(client, ADMIN_A, "Admin safety")
        client.post(
            f"/v1/tenants/{tenant}/members",
            headers=headers(ADMIN_A),
            json={"user_id": str(ADMIN_B), "role": "enterprise_admin"},
        )
        own = f"/v1/tenants/{tenant}/members/{ADMIN_A}/status"
        assert (
            client.patch(own, json={"active": False}, headers=headers(ADMIN_A)).status_code == 409
        )
        assert (
            client.patch(
                f"/v1/tenants/{tenant}/members/{ADMIN_B}/status",
                json={"active": False},
                headers=headers(ADMIN_A),
            ).status_code
            == 200
        )
        assert (
            client.patch(own, json={"active": False}, headers=headers(ADMIN_B)).status_code == 404
        )


def test_existing_cookie_loses_tenant_access_on_suspension(tmp_path, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_MASTER_KEY", "synthetic-session-suspension-key")
    app = create_app("sqlite://", storage_root=str(tmp_path), account_auth=True)
    with TestClient(app) as admin, TestClient(app) as member:
        owner = admin.post(
            "/v1/auth/register",
            json={
                "email": "owner@example.test",
                "password": "test-owner-password",
                "organization_name": "Owner org",
            },
        ).json()
        invited = member.post(
            "/v1/auth/register",
            json={"email": "member@example.test", "password": "test-member-password"},
        ).json()
        tenant, user_id = owner["tenant_id"], invited["user_id"]
        invitation = admin.post(
            f"/v1/tenants/{tenant}/invitations",
            json={"email": "member@example.test", "role": "member"},
        ).json()
        assert (
            admin.post(
                f"/v1/tenants/{tenant}/members", json={"user_id": user_id, "role": "member"}
            ).status_code
            == 201
        )
        assert member.get(f"/v1/tenants/{tenant}/spaces").status_code == 200
        assert (
            admin.patch(
                f"/v1/tenants/{tenant}/members/{user_id}/status", json={"active": False}
            ).status_code
            == 200
        )
        assert member.get(f"/v1/tenants/{tenant}/spaces").status_code == 404
        assert member.get("/v1/auth/me").status_code == 200
        assert (
            member.post(
                f"/v1/auth/invitations/{tenant}/accept", json={"token": invitation["invite_token"]}
            ).status_code
            == 409
        )


def test_membership_migration_preserves_existing_members_and_space_grants(tmp_path):
    database = tmp_path / "legacy.db"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "20261022_0010")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO tenants (id, name, status) VALUES ('a', 'Legacy', 'active')"
        )
        connection.execute(
            "INSERT INTO users (id, external_subject, status) VALUES ('b', 'legacy-user', 'active')"
        )
        connection.execute(
            "INSERT INTO memberships (tenant_id, user_id, role) "
            "VALUES ('a', 'b', 'enterprise_admin')"
        )
        connection.execute(
            "INSERT INTO spaces (id, tenant_id, name, classification) "
            "VALUES ('c', 'a', 'Old space', 'internal')"
        )
        connection.execute(
            "INSERT INTO space_memberships (tenant_id, space_id, user_id, role) "
            "VALUES ('a', 'c', 'b', 'manager')"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT active FROM memberships").fetchall() == [(1,)]
        assert connection.execute("SELECT role FROM space_memberships").fetchall() == [("manager",)]


def test_patch_is_allowed_for_local_frontend_cors(tmp_path):
    app = create_app("sqlite://", storage_root=str(tmp_path))
    response = TestClient(app).options(
        "/v1/tenants/test/members/test/status",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Content-Type,X-Dev-User-Id",
        },
    )
    assert response.status_code == 200
