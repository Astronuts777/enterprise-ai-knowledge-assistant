from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from enterprise_ai_api.crypto import FILE_MAGIC, decrypt_bytes
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


def test_invitation_only_grants_access_to_the_matching_account() -> None:
    app = create_app("sqlite://", account_auth=True)
    with TestClient(app) as admin, TestClient(app) as invited, TestClient(app) as outsider:
        owner = register(admin, "owner@example.com", organization_name="Acme")
        tenant_id = owner["tenant_id"]
        invitation = admin.post(
            f"/v1/tenants/{tenant_id}/invitations",
            json={"email": "member@example.com", "role": "member"},
        )
        assert invitation.status_code == 201
        token = invitation.json()["invite_token"]

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
    engine.dispose()

    assert question.startswith("enc:v1:")
    assert answer.startswith("enc:v1:")
    assert "Legacy secret" not in question
    assert "Legacy secret" not in answer
    encrypted_file = (storage_root / encrypted_key).read_bytes()
    context = (
        b"00000000-0000-0000-0000-000000000001:"
        b"00000000-0000-0000-0000-000000000005:"
        b"00000000-0000-0000-0000-000000000006"
    )
    assert encrypted_file.startswith(FILE_MAGIC)
    assert decrypt_bytes(encrypted_file, context=context) == b"Legacy file secret"
    assert not legacy_file.exists()
