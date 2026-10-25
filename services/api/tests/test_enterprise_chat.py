"""All chat tests use synthetic identities, isolated enterprises and temporary storage."""

import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text, update
from test_auth_and_encryption import register
from test_documents_api import create_workspace, headers
from test_worker_federation_migration import config_for

from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import (
    AuthSession,
    ChatConversation,
    ChatMessage,
    Membership,
    Tenant,
    User,
)


def environment(tmp_path, *, persistent=False):
    app = create_app(
        f"sqlite:///{tmp_path / 'chat.db'}" if persistent else "sqlite://",
        storage_root=str(tmp_path / "uploads"),
    )
    client = TestClient(app)
    owner, alice, bob, outsider = (uuid4() for _ in range(4))
    tenant, space = create_workspace(client, owner, "Isolated chat company")
    other, _ = create_workspace(client, outsider, "Unrelated test company")
    for person in (alice, bob):
        assert (
            client.post(
                f"/v1/tenants/{tenant}/members",
                headers=headers(owner),
                json={"user_id": str(person), "role": "member"},
            ).status_code
            == 201
        )
    base = f"/v1/tenants/{tenant}/chat"
    return SimpleNamespace(
        app=app,
        c=client,
        owner=owner,
        alice=alice,
        bob=bob,
        outsider=outsider,
        tenant=tenant,
        other=other,
        space=space,
        base=base,
    )


@pytest.fixture
def env(tmp_path):
    e = environment(tmp_path)
    yield e
    e.c.close()
    e.app.state.engine.dispose()


def open_chat(e, actor=None, peer=None):
    response = e.c.post(
        e.base + "/conversations",
        headers=headers(actor or e.alice),
        json={"peer_user_id": str(peer) if peer else None},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def send(e, conversation, body="Synthetic hello", actor=None, client_id=None):
    return e.c.post(
        e.base + f"/conversations/{conversation}/messages",
        headers=headers(actor or e.alice),
        json={"body": body, "client_id": str(client_id or uuid4())},
    )


def read(e, conversation, actor=None, **params):
    return e.c.get(
        e.base + f"/conversations/{conversation}/messages",
        headers=headers(actor or e.alice),
        params=params,
    )


def test_lobby_encryption_idempotency_and_no_ai_ingestion(env):
    e = env
    room = open_chat(e)
    assert room == open_chat(e, e.bob) == open_chat(e, e.owner)
    request_id = uuid4()
    body = "仅测试企业：明天下午三点开会。<script>alert('test')</script>"
    first = send(e, room, body, client_id=request_id)
    assert first.status_code == 200
    assert send(e, room, body, client_id=request_id).json() == first.json()
    assert send(e, room, "changed", client_id=request_id).status_code == 409
    reply = send(e, room, "收到", actor=e.bob)
    assert reply.json()["sequence"] == 2
    assert read(e, room, e.owner).json()["messages"][0]["body"] == body
    assert read(e, room).headers["cache-control"] == "no-store"
    with e.app.state.engine.connect() as connection:
        raw = connection.execute(text("SELECT body FROM chat_messages")).scalars().all()
        assert all(value.startswith("enc:v1:") and body not in value for value in raw)
        assert body not in str(connection.execute(text("SELECT details FROM audit_events")).all())
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar() == 0
    listing = e.c.get(e.base + "/conversations", headers=headers(e.bob))
    assert listing.status_code == 200 and "body" not in listing.text


def test_private_pair_is_symmetric_and_owner_cannot_read(env):
    e = env
    room = open_chat(e, peer=e.bob)
    assert room == open_chat(e, e.bob, e.alice)
    assert send(e, room).status_code == 200
    assert read(e, room, e.bob).status_code == 200
    assert read(e, room, e.owner).status_code == 404
    assert send(e, room, actor=e.owner).status_code == 404
    assert e.c.get(e.base + "/conversations", headers=headers(e.owner)).json() == []
    for actor in (e.owner, e.outsider):
        # Invalid ciphertext must never be decrypted for an unauthorized reader.
        with e.app.state.engine.begin() as connection:
            connection.execute(text("UPDATE chat_messages SET body='enc:v1:corrupt'"))
        assert read(e, room, actor).status_code == 404


def test_new_member_only_sees_lobby_history_and_conversation_pagination(env):
    e = env
    lobby = open_chat(e)
    private = open_chat(e, peer=e.bob)
    assert send(e, lobby, "Public historical test message").status_code == 200
    assert send(e, private, "Private historical test message").status_code == 200
    newcomer = uuid4()
    assert (
        e.c.post(
            f"/v1/tenants/{e.tenant}/members",
            headers=headers(e.owner),
            json={"user_id": str(newcomer), "role": "member"},
        ).status_code
        == 201
    )
    assert (
        read(e, lobby, newcomer).json()["messages"][0]["body"] == "Public historical test message"
    )
    assert read(e, private, newcomer).status_code == 404
    ids = []
    for offset in range(2):
        response = e.c.get(
            e.base + "/conversations",
            headers=headers(e.alice),
            params={"limit": 1, "offset": offset},
        )
        ids.extend(item["id"] for item in response.json())
    assert set(ids) == {lobby, private}


def test_inactive_enterprise_denied_and_timestamps_have_timezone(env):
    e = env
    room = open_chat(e)
    assert send(e, room).json()["created_at"].endswith("Z")
    with e.app.state.session_factory() as session:
        session.execute(
            update(Tenant).where(Tenant.id == UUID(e.tenant)).values(status="suspended")
        )
        session.commit()
    assert read(e, room).status_code == 404
    assert send(e, room).status_code == 404


def test_cross_tenant_guessed_ids_and_no_auth_fail_closed(env):
    e = env
    room = open_chat(e, peer=e.bob)
    assert send(e, room).status_code == 200
    for peer in (e.alice, e.outsider, uuid4()):
        assert (
            e.c.post(
                e.base + "/conversations",
                headers=headers(e.alice),
                json={"peer_user_id": str(peer)},
            ).status_code
            == 404
        )
    other_base = f"/v1/tenants/{e.other}/chat"
    path = f"/conversations/{room}/messages"
    assert e.c.get(other_base + path, headers=headers(e.outsider)).status_code == 404
    assert (
        e.c.post(
            other_base + path,
            headers=headers(e.outsider),
            json={"body": "no", "client_id": str(uuid4())},
        ).status_code
        == 404
    )
    assert e.c.get(e.base + path).status_code == 401
    assert e.c.get(e.base + "/conversations", headers=headers(e.outsider)).status_code == 404


@pytest.mark.parametrize("body", ["", " \n\t ", "a" * 4001, "a\x00b"])
def test_invalid_content_is_not_echoed_or_saved(env, body):
    e = env
    room = open_chat(e)
    response = send(e, room, body)
    assert response.status_code == 422
    assert "input" not in response.text
    assert read(e, room).json()["messages"] == []


def test_cannot_spoof_sender_or_add_attachment(env):
    e = env
    room = open_chat(e)
    for extra in (
        {"sender_id": str(e.owner)},
        {"tenant_id": e.other},
        {"document_id": str(uuid4())},
    ):
        response = e.c.post(
            e.base + f"/conversations/{room}/messages",
            headers=headers(e.alice),
            json={"body": "test", "client_id": str(uuid4()), **extra},
        )
        assert response.status_code == 422
    assert read(e, room).json()["messages"] == []


@pytest.mark.parametrize("field", ["active", "provisioning_active", "user_status", "mfa"])
def test_revoked_member_cannot_read_or_write_existing_chat(env, field):
    e = env
    room = open_chat(e, peer=e.bob)
    assert send(e, room).status_code == 200
    with e.app.state.session_factory() as session:
        if field in {"active", "provisioning_active"}:
            session.execute(
                update(Membership)
                .where(Membership.tenant_id == UUID(e.tenant), Membership.user_id == e.alice)
                .values(**{field: False})
            )
        elif field == "user_status":
            session.execute(update(User).where(User.id == e.alice).values(status="suspended"))
        else:
            session.execute(
                update(Tenant).where(Tenant.id == UUID(e.tenant)).values(mfa_policy="all")
            )
        session.commit()
    assert read(e, room).status_code in (403, 404)
    assert send(e, room).status_code in (403, 404)
    assert e.c.get(e.base + "/conversations", headers=headers(e.alice)).status_code in (403, 404)
    assert e.c.post(e.base + "/conversations", headers=headers(e.alice), json={}).status_code in (
        403,
        404,
    )


def test_inactive_peer_preserves_authorized_history_but_stops_sends(env):
    e = env
    room = open_chat(e, peer=e.bob)
    assert send(e, room).status_code == 200
    response = e.c.patch(
        f"/v1/tenants/{e.tenant}/members/{e.bob}/status",
        headers=headers(e.owner),
        json={"active": False},
    )
    assert response.status_code == 200
    history = read(e, room).json()
    assert len(history["messages"]) == 1 and not history["conversation"]["can_send"]
    assert send(e, room).status_code == 409
    assert read(e, room, e.bob).status_code == 404


def test_keyset_pagination_no_skips_and_latest_window(env):
    e = env
    room = open_chat(e)
    with e.app.state.session_factory() as session:
        for sequence in range(1, 126):
            session.add(
                ChatMessage(
                    tenant_id=UUID(e.tenant),
                    conversation_id=UUID(room),
                    sequence=sequence,
                    sender_id=e.bob,
                    client_id=uuid4(),
                    body=str(sequence),
                )
            )
        session.get(ChatConversation, UUID(room)).last_sequence = 125
        session.commit()
    latest = read(e, room).json()
    assert [m["sequence"] for m in latest["messages"]] == list(range(76, 126))
    assert latest["has_more"]
    older = read(e, room, before=76).json()
    assert [m["sequence"] for m in older["messages"]] == list(range(26, 76))
    assert read(e, room, before=26).json()["has_more"] is False
    assert [m["sequence"] for m in read(e, room, after=0).json()["messages"]] == list(range(1, 51))
    assert send(e, room).json()["sequence"] == 126
    assert [m["sequence"] for m in read(e, room, after=125).json()["messages"]] == [126]
    for params in ({"after": 0, "before": 10}, {"limit": 101}, {"before": -1}):
        assert read(e, room, **params).status_code == 422


def test_rate_limit_is_shared_across_conversations_and_retries_are_free(env):
    e = env
    room = open_chat(e)
    direct = open_chat(e, peer=e.bob)
    client_id = uuid4()
    for index in range(30):
        assert send(e, room, client_id=client_id if index == 0 else None).status_code == 200
    assert send(e, room, client_id=client_id).status_code == 200
    response = send(e, direct)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert send(e, room, actor=e.bob).status_code == 200


def test_membership_and_document_acl_unchanged(env):
    e = env
    base = f"/v1/tenants/{e.tenant}"
    document = e.c.post(
        base + f"/spaces/{e.space}/documents",
        headers=headers(e.owner),
        data={"access_mode": "restricted"},
        files={"file": ("secret.txt", b"Synthetic confidential content")},
    ).json()
    before = e.c.get(base + "/members", headers=headers(e.owner)).json()
    room = open_chat(e, e.owner, e.alice)
    assert send(e, room, "Please review the document", actor=e.owner).status_code == 200
    assert read(e, room).status_code == 200
    doc_id = document["document"]["id"]
    assert e.c.get(base + f"/documents/{doc_id}/content", headers=headers(e.alice)).status_code in (
        403,
        404,
    )
    assert e.c.get(base + "/members", headers=headers(e.owner)).json() == before


def test_account_cookie_revocation_and_dev_header_cannot_bypass(tmp_path):
    app = create_app("sqlite://", account_auth=True, storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        account = register(client, "chat-owner@example.test", organization_name="Test chat account")
        base = f"/v1/tenants/{account['tenant_id']}/chat"
        room = client.post(base + "/conversations", json={}).json()["id"]
        path = base + f"/conversations/{room}/messages"
        assert (
            client.post(path, json={"body": "Synthetic", "client_id": str(uuid4())}).status_code
            == 200
        )
        with app.state.session_factory() as session:
            session.execute(
                update(AuthSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            session.commit()
        assert client.get(path, headers=headers(UUID(account["user_id"]))).status_code == 401
        assert (
            client.post(
                path,
                headers=headers(UUID(account["user_id"])),
                json={"body": "no", "client_id": str(uuid4())},
            ).status_code
            == 401
        )


def test_parallel_pair_creation_sends_and_duplicate_retries(tmp_path):
    e = environment(tmp_path, persistent=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rooms = list(
                pool.map(lambda pair: open_chat(e, *pair), [(e.alice, e.bob), (e.bob, e.alice)])
            )
            assert rooms[0] == rooms[1]
            request_id = uuid4()
            responses = list(pool.map(lambda _: send(e, rooms[0], client_id=request_id), range(2)))
            assert all(r.status_code == 200 for r in responses)
            assert responses[0].json()["id"] == responses[1].json()["id"]
            replies = list(
                pool.map(lambda person: send(e, rooms[0], actor=person), [e.alice, e.bob])
            )
            assert sorted(r.json()["sequence"] for r in replies) == [2, 3]
        assert len(read(e, rooms[0]).json()["messages"]) == 3
    finally:
        e.c.close()
        e.app.state.engine.dispose()


def test_chat_migration_preserves_legacy_and_generates_rls(tmp_path):
    path = tmp_path / "legacy.db"
    cfg = config_for(f"sqlite:///{path}")
    command.upgrade(cfg, "20261023_0014")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO tenants (id,name,status) VALUES ('a','Legacy','active')")
        before = connection.execute("SELECT * FROM tenants").fetchall()
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM tenants").fetchall() == before
        assert connection.execute("SELECT count(*) FROM chat_conversations").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    stream = io.StringIO()
    command.upgrade(
        config_for("postgresql+psycopg://unused/unused", stream), "20261023_0014:head", sql=True
    )
    sql = stream.getvalue()
    for table in ("chat_conversations", "chat_messages"):
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY tenant_isolation ON {table}" in sql
    assert "fk_chat_message_conversation" in sql
