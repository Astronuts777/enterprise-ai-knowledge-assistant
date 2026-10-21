from pathlib import Path
from urllib.parse import parse_qs, urlparse

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from enterprise_ai_api.crypto import FILE_MAGIC, decrypt_bytes
from enterprise_ai_api.email_delivery import MemoryEmailSender
from enterprise_ai_api.main import create_app

PASSWORD = "correct horse battery staple"


def register(
    client: TestClient,
    email: str,
    *,
    organization_name: str | None = None,
) -> dict[str, str | None]:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "organization_name": organization_name,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_account_registration_login_and_server_side_logout() -> None:
    app = create_app("sqlite://", account_auth=True)
    with TestClient(app) as client:
        account = register(client, "owner@example.com", organization_name="Acme")
        tenant_id = account["tenant_id"]
        assert tenant_id

        me = client.get("/v1/auth/me")
        tenant = client.get(f"/v1/tenants/{tenant_id}")
        logged_out = client.post("/v1/auth/logout")
        rejected = client.get("/v1/auth/me")
        logged_in = client.post(
            "/v1/auth/login",
            json={"email": "OWNER@example.com", "password": PASSWORD},
        )

    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"
    assert tenant.status_code == 200
    assert logged_out.status_code == 204
    assert rejected.status_code == 401
    assert logged_in.status_code == 200


def test_user_can_review_and_revoke_device_sessions() -> None:
    app = create_app("sqlite://", account_auth=True)
    chrome_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
        )
    }
    firefox_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/143.0"
    }
    with TestClient(app, headers=chrome_headers) as current, TestClient(
        app, headers=firefox_headers
    ) as other, TestClient(app) as revoked:
        register(current, "devices@example.com", organization_name="Acme")
        signed_in = other.post(
            "/v1/auth/login",
            json={"email": "devices@example.com", "password": PASSWORD},
        )
        assert signed_in.status_code == 200
        other_cookie = other.cookies.get("enterprise_ai_session")
        assert other_cookie
        revoked.cookies.set("enterprise_ai_session", other_cookie)

        sessions = current.get("/v1/auth/sessions")
        other_session = next(item for item in sessions.json() if not item["is_current"])
        removed = current.delete(f"/v1/auth/sessions/{other_session['id']}")
        rejected = revoked.get("/v1/auth/me")
        current_still_valid = current.get("/v1/auth/me")

    assert sessions.status_code == 200
    assert {item["client_label"] for item in sessions.json()} == {
        "Chrome · macOS",
        "Firefox · Windows",
    }
    assert sum(item["is_current"] for item in sessions.json()) == 1
    assert removed.status_code == 204
    assert rejected.status_code == 401
    assert current_still_valid.status_code == 200


def test_user_can_revoke_other_sessions_and_admin_can_force_member_logout() -> None:
    app = create_app("sqlite://", account_auth=True)
    with (
        TestClient(app) as admin,
        TestClient(app) as second_admin_session,
        TestClient(app) as member,
    ):
        owner = register(admin, "owner@example.com", organization_name="Acme")
        tenant_id = owner["tenant_id"]
        second_admin_session.post(
            "/v1/auth/login",
            json={"email": "owner@example.com", "password": PASSWORD},
        )
        revoked_others = admin.delete("/v1/auth/sessions")
        second_rejected = second_admin_session.get("/v1/auth/me")

        member_account = register(member, "member@example.com")
        member_id = member_account["user_id"]
        invitation = admin.post(
            f"/v1/tenants/{tenant_id}/invitations",
            json={"email": "member@example.com", "role": "member"},
        )
        accepted = member.post(
            f"/v1/auth/invitations/{tenant_id}/accept",
            json={"token": invitation.json()["invite_token"]},
        )
        forced = admin.delete(
            f"/v1/tenants/{tenant_id}/members/{member_id}/sessions"
        )
        member_rejected = member.get("/v1/auth/me")
        audit = admin.get(
            f"/v1/tenants/{tenant_id}/audit-events?action="
            "account.sessions_revoked_by_admin"
        )

    assert revoked_others.status_code == 200
    assert revoked_others.json()["revoked_count"] == 1
    assert second_rejected.status_code == 401
    assert invitation.status_code == 201
    assert accepted.status_code == 200
    assert forced.status_code == 200
    assert forced.json()["revoked_count"] == 1
    assert member_rejected.status_code == 401
    assert audit.status_code == 200
    assert audit.json()[0]["resource_id"] == member_id
    assert "email" not in str(audit.json()[0]["details"])


def test_admin_cannot_revoke_account_active_in_another_tenant() -> None:
    app = create_app("sqlite://", account_auth=True)
    with TestClient(app) as admin, TestClient(app) as external_owner:
        owner = register(admin, "owner@example.com", organization_name="Acme")
        external = register(
            external_owner,
            "external@example.com",
            organization_name="External company",
        )
        saved = admin.post(
            f"/v1/tenants/{owner['tenant_id']}/members",
            json={"user_id": external["user_id"], "role": "member"},
        )
        refused = admin.delete(
            f"/v1/tenants/{owner['tenant_id']}/members/"
            f"{external['user_id']}/sessions"
        )
        still_active = external_owner.get("/v1/auth/me")

    assert saved.status_code == 201
    assert refused.status_code == 404
    assert still_active.status_code == 200


def test_invitation_only_grants_access_to_the_matching_account() -> None:
    sender = MemoryEmailSender()
    app = create_app("sqlite://", account_auth=True, email_sender=sender)
    with TestClient(app) as admin, TestClient(app) as invited, TestClient(app) as outsider:
        owner = register(admin, "owner@example.com", organization_name="Acme")
        tenant_id = owner["tenant_id"]
        invitation = admin.post(
            f"/v1/tenants/{tenant_id}/invitations",
            json={"email": "member@example.com", "role": "member"},
        )
        assert invitation.status_code == 201
        assert invitation.json()["delivery_status"] == "sent"
        assert sender.messages[-1].recipient == "member@example.com"
        token = invitation.json()["invite_token"]
        assert token in sender.messages[-1].text

        register(invited, "member@example.com")
        accepted = invited.post(
            f"/v1/auth/invitations/{tenant_id}/accept",
            json={"token": token},
        )
        member_read = invited.get(f"/v1/tenants/{tenant_id}")

        register(outsider, "outsider@example.com")
        outsider_accept = outsider.post(
            f"/v1/auth/invitations/{tenant_id}/accept",
            json={"token": token},
        )

    assert accepted.status_code == 200
    assert accepted.json()["tenant_id"] == tenant_id
    assert member_read.status_code == 200
    assert outsider_accept.status_code in {403, 404}


def token_from_latest_email(sender: MemoryEmailSender) -> str:
    link = next(line for line in sender.messages[-1].text.splitlines() if line.startswith("http"))
    return parse_qs(urlparse(link).query)["token"][0]


def test_email_verification_creates_session_only_after_token_is_consumed() -> None:
    sender = MemoryEmailSender()
    app = create_app(
        "sqlite://",
        account_auth=True,
        email_sender=sender,
        require_email_verification=True,
    )
    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": "verified@example.com",
                "password": PASSWORD,
                "organization_name": "Verified Acme",
            },
        )
        before = client.get("/v1/auth/me")
        verified = client.post(
            "/v1/auth/email/verify",
            json={"token": token_from_latest_email(sender)},
        )
        reused = client.post(
            "/v1/auth/email/verify",
            json={"token": token_from_latest_email(sender)},
        )
        after = client.get("/v1/auth/me")

    assert registered.status_code == 201
    assert registered.json()["email_verified"] is False
    assert before.status_code == 401
    assert verified.status_code == 200
    assert verified.json()["email_verified"] is True
    assert reused.status_code == 400
    assert after.status_code == 200


def test_password_reset_email_is_generic_and_revokes_previous_sessions() -> None:
    sender = MemoryEmailSender()
    app = create_app("sqlite://", account_auth=True, email_sender=sender)
    with TestClient(app) as client, TestClient(app) as old_session:
        register(client, "reset@example.com", organization_name="Acme")
        old_cookie = client.cookies.get("enterprise_ai_session")
        assert old_cookie
        old_session.cookies.set("enterprise_ai_session", old_cookie)
        unknown = client.post(
            "/v1/auth/password/forgot", json={"email": "unknown@example.com"}
        )
        requested = client.post(
            "/v1/auth/password/forgot", json={"email": "reset@example.com"}
        )
        reset = client.post(
            "/v1/auth/password/reset",
            json={"token": token_from_latest_email(sender), "password": "new secure password 123"},
        )
        new_cookie = client.cookies.get("enterprise_ai_session")
        revoked_session = old_session.get("/v1/auth/me")
        old_login = client.post(
            "/v1/auth/login",
            json={"email": "reset@example.com", "password": PASSWORD},
        )
        new_login = client.post(
            "/v1/auth/login",
            json={"email": "reset@example.com", "password": "new secure password 123"},
        )

    assert unknown.status_code == requested.status_code == 200
    assert unknown.json() == requested.json()
    assert reset.status_code == 200
    assert old_cookie != new_cookie
    assert revoked_session.status_code == 401
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_login_is_rate_limited_after_repeated_failures() -> None:
    app = create_app("sqlite://", account_auth=True)
    with TestClient(app) as client:
        register(client, "limited@example.com")
        client.post("/v1/auth/logout")
        failures = [
            client.post(
                "/v1/auth/login",
                json={"email": "limited@example.com", "password": "wrong"},
            )
            for _ in range(6)
        ]

    assert [response.status_code for response in failures[:5]] == [401] * 5
    assert failures[5].status_code == 429
    assert failures[5].headers["retry-after"] == "900"


def test_document_content_is_encrypted_in_database_and_file_storage(tmp_path: Path) -> None:
    storage_root = tmp_path / "uploads"
    app = create_app("sqlite://", storage_root=str(storage_root), account_auth=True)
    plaintext = b"The confidential launch code is ORCHID."
    with TestClient(app) as client:
        account = register(client, "owner@example.com", organization_name="Acme")
        tenant_id = account["tenant_id"]
        space = client.post(
            f"/v1/tenants/{tenant_id}/spaces",
            json={"name": "Restricted", "classification": "restricted"},
        ).json()
        uploaded = client.post(
            f"/v1/tenants/{tenant_id}/spaces/{space['id']}/documents",
            files={"file": ("secret.txt", plaintext, "text/plain")},
        )
        assert uploaded.status_code == 201
        result = uploaded.json()
        document_id = result["document"]["id"]
        version_id = result["version"]["id"]

        with app.state.engine.connect() as connection:
            raw_chunk = connection.exec_driver_sql(
                "SELECT text FROM document_chunks LIMIT 1"
            ).scalar_one()
            object_key = connection.exec_driver_sql(
                "SELECT object_key FROM document_versions LIMIT 1"
            ).scalar_one()

    encrypted_file = (storage_root / object_key).read_bytes()
    context = f"{tenant_id}:{document_id}:{version_id}".encode()
    assert raw_chunk.startswith("enc:v1:")
    assert "ORCHID" not in raw_chunk
    assert encrypted_file.startswith(FILE_MAGIC)
    assert plaintext not in encrypted_file
    assert decrypt_bytes(encrypted_file, context=context) == plaintext


def test_migration_encrypts_existing_knowledge_rows(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "legacy.db"
    storage_root = tmp_path / "legacy-uploads"
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(storage_root))
    database_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "20261021_0003")

    engine = create_engine(database_url)
    tenant_id = "00000000000000000000000000000001"
    user_id = "00000000000000000000000000000002"
    answer_id = "00000000000000000000000000000003"
    space_id = "00000000000000000000000000000004"
    document_id = "00000000000000000000000000000005"
    version_id = "00000000000000000000000000000006"
    legacy_key = "legacy/secret.txt"
    legacy_file = storage_root / legacy_key
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(b"Legacy file secret")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO tenants (id, name, status) VALUES (?, ?, ?)",
            (tenant_id, "Legacy", "active"),
        )
        connection.exec_driver_sql(
            "INSERT INTO users (id, external_subject, status) VALUES (?, ?, ?)",
            (user_id, "legacy:user", "active"),
        )
        connection.exec_driver_sql(
            "INSERT INTO memberships (tenant_id, user_id, role) VALUES (?, ?, ?)",
            (tenant_id, user_id, "enterprise_admin"),
        )
        connection.exec_driver_sql(
            "INSERT INTO knowledge_answers "
            "(id, tenant_id, created_by, question, answer, status) VALUES (?, ?, ?, ?, ?, ?)",
            (
                answer_id,
                tenant_id,
                user_id,
                "Legacy secret question",
                "Legacy secret answer",
                "grounded",
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO spaces (id, tenant_id, name, classification) VALUES (?, ?, ?, ?)",
            (space_id, tenant_id, "Legacy space", "restricted"),
        )
        connection.exec_driver_sql(
            "INSERT INTO documents (id, tenant_id, space_id, title, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (document_id, tenant_id, space_id, "Legacy document", user_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO document_versions "
            "(id, tenant_id, document_id, version_number, filename, media_type, byte_size, "
            "content_hash, object_key, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                tenant_id,
                document_id,
                1,
                "secret.txt",
                "text/plain",
                18,
                "legacy-sha256",
                legacy_key,
                "published",
                user_id,
            ),
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    with engine.connect() as connection:
        question, answer = connection.exec_driver_sql(
            "SELECT question, answer FROM knowledge_answers WHERE id = ?", (answer_id,)
        ).one()
        encrypted_key = connection.exec_driver_sql(
            "SELECT object_key FROM document_versions WHERE id = ?", (version_id,)
        ).scalar_one()
        space_role = connection.exec_driver_sql(
            "SELECT role FROM space_memberships WHERE tenant_id = ? AND space_id = ? "
            "AND user_id = ?",
            (tenant_id, space_id, user_id),
        ).scalar_one()
    engine.dispose()

    assert question.startswith("enc:v1:")
    assert answer.startswith("enc:v1:")
    assert "Legacy secret" not in question
    assert "Legacy secret" not in answer
    assert space_role == "manager"
    encrypted_file = (storage_root / encrypted_key).read_bytes()
    context = (
        b"00000000-0000-0000-0000-000000000001:"
        b"00000000-0000-0000-0000-000000000005:"
        b"00000000-0000-0000-0000-000000000006"
    )
    assert encrypted_file.startswith(FILE_MAGIC)
    assert decrypt_bytes(encrypted_file, context=context) == b"Legacy file secret"
    assert not legacy_file.exists()
