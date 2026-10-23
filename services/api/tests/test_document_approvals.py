"""Approval tests use only synthetic isolated tenants, identities, and documents."""

import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from test_documents_api import create_workspace, headers
from test_worker_federation_migration import config_for

from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentApproval, DocumentVersion, Tenant, User


def workspace(tmp_path, *, restricted=True, persistent=False):
    owner, editor, admin, reader, outsider = (uuid4() for _ in range(5))
    app = create_app(
        f"sqlite:///{tmp_path / 'approval.db'}" if persistent else "sqlite://",
        storage_root=str(tmp_path / "uploads"),
    )
    client = TestClient(app)
    tenant, space = create_workspace(client, owner, "Isolated approval company")
    base = f"/v1/tenants/{tenant}"
    for person, role in (
        (editor, "member" if restricted else "knowledge_admin"),
        (admin, "enterprise_admin"),
        (reader, "member"),
    ):
        assert (
            client.post(
                base + "/members",
                headers=headers(owner),
                json={
                    "user_id": str(person),
                    "role": role,
                },
            ).status_code
            == 201
        )
    other, _ = create_workspace(client, outsider, "Other isolated company")
    item = client.post(
        base + f"/spaces/{space}/documents",
        headers=headers(owner),
        data={"access_mode": "restricted" if restricted else "space"},
        files={"file": ("policy.txt", b"Original policy requires Alice.")},
    ).json()
    doc, original = item["document"]["id"], item["version"]["id"]
    version_base = base + "/document-versions/"
    assert (
        client.post(version_base + original + "/publish", headers=headers(owner)).status_code == 200
    )
    for person, role in ((editor, "editor"), (reader, "reader")):
        if restricted:
            result = client.put(
                base + f"/documents/{doc}/grants/{person}",
                headers=headers(owner),
                json={"role": role},
            )
        else:
            result = client.post(
                base + f"/spaces/{space}/members",
                headers=headers(owner),
                json={"user_id": str(person), "role": "manager" if role == "editor" else "viewer"},
            )
        assert result.status_code in (200, 201)
    draft = client.post(
        base + f"/documents/{doc}/versions",
        headers=headers(editor),
        files={"file": ("revised.txt", b"Revised policy requires Bob.")},
    )
    assert draft.status_code == 201, draft.text
    version = draft.json()["id"]
    path = version_base + version
    assert client.post(path + "/enable-approval", headers=headers(owner)).status_code == 200
    return SimpleNamespace(
        c=client,
        app=app,
        base=base,
        path=path,
        doc=doc,
        version=version,
        original=original,
        owner=owner,
        editor=editor,
        admin=admin,
        reader=reader,
        outsider=outsider,
        other=other,
        tenant=tenant,
    )


@pytest.fixture
def env(tmp_path):
    value = workspace(tmp_path)
    yield value
    value.c.close()


def submit(e, note="Synthetic change description"):
    response = e.c.post(e.path + "/approvals", headers=headers(e.editor), json={"note": note})
    assert response.status_code == 201, response.text
    return response.json()


def decision(e, approval, value="approved", actor=None, note="Synthetic review opinion"):
    return e.c.post(
        e.base + f"/approvals/{approval['id']}/decision",
        headers=headers(actor or e.owner),
        json={"decision": value, "note": note},
    )


def test_approval_gate_exact_version_and_encryption(env, tmp_path):
    e = env
    encrypted_files = {p: p.read_bytes() for p in (tmp_path / "uploads").rglob("*.enc")}
    assert encrypted_files and all(data.startswith(b"EAKA1") for data in encrypted_files.values())
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
    request = submit(e)
    assert (
        e.c.post(
            e.path + "/approvals", headers=headers(e.editor), json={"note": "duplicate"}
        ).status_code
        == 409
    )
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
    assert decision(e, request).status_code == 200
    # Approval alone must not publish or leak drafts to a read-only member.
    content = e.c.get(e.base + f"/documents/{e.doc}/content", headers=headers(e.reader)).json()
    assert content["version"]["id"] == e.original
    assert e.c.post(e.path + "/publish", headers=headers(e.editor)).status_code == 403
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 200
    assert (
        e.c.get(e.base + f"/documents/{e.doc}/content", headers=headers(e.reader)).json()[
            "version"
        ]["id"]
        == e.version
    )
    assert (
        e.c.get(e.base + "/approvals", headers=headers(e.owner)).json()[0]["status"] == "published"
    )
    # Repeating the same publish is idempotent, not a second approval consumption.
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 200
    assert e.c.post(e.path + "/unpublish", headers=headers(e.owner)).status_code == 200
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
    second = submit(e)
    assert second["attempt"] == 2
    with e.app.state.engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT submission_note, review_note FROM document_approvals "
                "WHERE status='published'"
            )
        ).one()
        assert all(value.startswith("enc:v1:") for value in row)
        audit = str(conn.execute(text("SELECT details FROM audit_events")).all())
        assert "Synthetic review opinion" not in audit
        assert "Synthetic change description" not in audit
    assert {p: p.read_bytes() for p in encrypted_files} == encrypted_files


def test_reject_cancel_resubmit_and_no_double_decisions(env):
    e = env
    first = submit(e)
    assert decision(e, first, "rejected", note=" ").status_code == 422
    assert decision(e, first, "rejected").status_code == 200
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
    assert decision(e, first).status_code == 409
    second = submit(e)
    assert second["attempt"] == 2
    cancel = e.base + f"/approvals/{second['id']}/cancel"
    assert e.c.post(cancel, headers=headers(e.editor)).status_code == 200
    assert decision(e, second).status_code == 409
    third = submit(e)
    assert decision(e, third).status_code == 200
    assert (
        e.c.post(e.base + f"/approvals/{third['id']}/cancel", headers=headers(e.owner)).status_code
        == 200
    )
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409


def test_restricted_acl_inbox_and_notes_filtered_before_decryption(env):
    e = env
    approval = submit(e)
    for actor in (e.reader, e.admin):
        assert e.c.get(e.base + "/approvals", headers=headers(actor)).json() == []
        assert e.c.post(e.path + "/enable-approval", headers=headers(actor)).status_code == 404
        assert (
            e.c.post(e.path + "/approvals", headers=headers(actor), json={"note": ""}).status_code
            == 404
        )
        assert decision(e, approval, actor=actor).status_code == 404
    assert e.c.get(e.base + "/approvals").status_code == 401
    assert e.c.get(e.base + "/approvals", headers=headers(e.outsider)).status_code == 404
    assert (
        e.c.post(
            f"/v1/tenants/{e.other}/approvals/{approval['id']}/decision",
            headers=headers(e.outsider),
            json={"decision": "approved", "note": "x"},
        ).status_code
        == 404
    )
    with e.app.state.engine.begin() as conn:
        conn.execute(text("UPDATE document_approvals SET submission_note='enc:v1:invalid'"))
    assert e.c.get(e.base + "/approvals", headers=headers(e.admin)).json() == []
    assert decision(e, approval, actor=e.admin).status_code == 404


def test_no_self_approval_or_approval_by_uploader(tmp_path):
    e = workspace(tmp_path, restricted=False)
    try:
        # Another submitter cannot disguise a self-review by the original author.
        request = e.c.post(
            e.path + "/approvals", headers=headers(e.admin), json={"note": ""}
        ).json()
        assert decision(e, request, actor=e.admin).status_code == 403
        assert decision(e, request, actor=e.editor).status_code == 403
        assert decision(e, request, actor=e.owner).status_code == 200
    finally:
        e.c.close()


def test_editor_cannot_enable_policy_decide_or_cancel_others(env):
    e = env
    assert e.c.post(e.path + "/enable-approval", headers=headers(e.editor)).status_code == 404
    request = e.c.post(e.path + "/approvals", headers=headers(e.owner), json={"note": ""}).json()
    assert decision(e, request, actor=e.owner).status_code == 403
    assert decision(e, request, actor=e.editor).status_code == 404
    assert (
        e.c.post(
            e.base + f"/approvals/{request['id']}/cancel", headers=headers(e.editor)
        ).status_code
        == 403
    )


def test_new_version_never_reuses_approved_previous_version(env):
    e = env
    approval = submit(e)
    assert decision(e, approval).status_code == 200
    newer = e.c.post(
        e.base + f"/documents/{e.doc}/versions",
        headers=headers(e.editor),
        files={"file": ("newer.txt", b"Third version requires Carol.")},
    ).json()
    assert (
        e.c.post(
            e.base + f"/document-versions/{newer['id']}/publish", headers=headers(e.owner)
        ).status_code
        == 409
    )
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 200


@pytest.mark.parametrize("when", ["pending", "approved"])
def test_submitter_revocation_prevents_approval_or_publication(env, when):
    e = env
    approval = submit(e)
    if when == "approved":
        assert decision(e, approval).status_code == 200
    assert (
        e.c.delete(
            e.base + f"/documents/{e.doc}/grants/{e.editor}", headers=headers(e.owner)
        ).status_code
        == 204
    )
    assert e.c.get(e.base + "/approvals", headers=headers(e.editor)).json() == []
    if when == "pending":
        assert decision(e, approval).status_code == 409
    assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
    assert (
        e.c.post(
            e.base + f"/approvals/{approval['id']}/cancel", headers=headers(e.owner)
        ).status_code
        == 200
    )


@pytest.mark.parametrize("change", ["suspend", "mfa", "restricted", "hash"])
def test_stale_approval_fails_closed_before_publication(tmp_path, change):
    e = workspace(tmp_path, restricted=False)
    try:
        approval = submit(e)
        assert decision(e, approval, actor=e.admin).status_code == 200
        if change == "suspend":
            assert (
                e.c.patch(
                    e.base + f"/members/{e.admin}/status",
                    headers=headers(e.owner),
                    json={"active": False},
                ).status_code
                == 200
            )
        elif change == "restricted":
            assert (
                e.c.put(
                    e.base + f"/documents/{e.doc}/access",
                    headers=headers(e.owner),
                    json={"access_mode": "restricted"},
                ).status_code
                == 200
            )
        elif change == "hash":
            with e.app.state.session_factory() as session:
                session.get(DocumentVersion, UUID(e.version)).content_hash = "changed"
                session.commit()
        else:
            with e.app.state.session_factory() as session:
                session.get(Tenant, UUID(e.tenant)).mfa_policy = "admins"
                session.get(User, e.owner).mfa_enabled_at = session.get(
                    DocumentVersion, UUID(e.version)
                ).created_at
                session.commit()
        assert e.c.post(e.path + "/publish", headers=headers(e.owner)).status_code == 409
        with e.app.state.session_factory() as session:
            assert session.get(DocumentApproval, UUID(approval["id"])).status == "approved"
    finally:
        e.c.close()


@pytest.mark.parametrize("state", ["queued", "processing", "failed", "published"])
def test_unready_or_published_versions_cannot_enter_review(env, state):
    e = env
    with e.app.state.session_factory() as session:
        session.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == UUID(e.version))
            .values(status=state)
        )
        session.commit()
    assert (
        e.c.post(e.path + "/approvals", headers=headers(e.editor), json={"note": ""}).status_code
        == 409
    )


def test_inbox_pagination_and_no_silent_truncation(env):
    e = env
    # Different UUID ordering must not hide the latest attempt when timestamps tie.
    for _index in range(3):
        approval = submit(e)
        assert decision(e, approval, "rejected").status_code == 200
    rows = e.c.get(e.base + "/approvals?status=rejected&limit=2", headers=headers(e.editor)).json()
    assert len(rows) == 2
    assert (
        len(
            e.c.get(
                e.base + "/approvals?status=rejected&offset=2&limit=2", headers=headers(e.editor)
            ).json()
        )
        == 1
    )
    rows = e.c.get(
        e.base + f"/approvals?version_id={e.version}&limit=1", headers=headers(e.editor)
    ).json()
    assert rows[0]["attempt"] == 3


def test_concurrent_submit_and_decisions_are_serialized(tmp_path):
    e = workspace(tmp_path, restricted=False, persistent=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(
                    lambda _: (
                        e.c.post(
                            e.path + "/approvals",
                            headers=headers(e.editor),
                            json={"note": ""},
                        ).status_code
                    ),
                    range(2),
                )
            )
        assert sorted(codes) == [201, 409]
        approval = e.c.get(e.base + "/approvals", headers=headers(e.owner)).json()[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(
                pool.map(
                    lambda actor: decision(e, approval, actor=actor).status_code,
                    [e.owner, e.admin],
                )
            )
        assert sorted(codes) == [200, 409]
        with e.app.state.session_factory() as session:
            assert len(list(session.scalars(select(DocumentApproval)))) == 1
    finally:
        e.c.close()


def test_approval_migration_preserves_existing_publication(tmp_path):
    path = tmp_path / "legacy.db"
    cfg = config_for(f"sqlite:///{path}")
    command.upgrade(cfg, "20261022_0013")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tenants (id,name,status) VALUES ('a','Legacy','active')")
        conn.execute("INSERT INTO users (id,external_subject,status) VALUES ('b','b','active')")
        conn.execute(
            "INSERT INTO spaces (id,tenant_id,name,classification) "
            "VALUES ('c','a','Space','internal')"
        )
        conn.execute(
            "INSERT INTO documents (id,tenant_id,space_id,title,created_by) "
            "VALUES ('d','a','c','Doc','b')"
        )
        conn.execute(
            "INSERT INTO document_versions (id,tenant_id,document_id,version_number,"
            "filename,media_type,byte_size,content_hash,object_key,status,created_by) "
            "VALUES ('e','a','d',1,'a.txt','text/plain',1,'hash','key','published','b')"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT approval_required FROM documents").fetchone() == (0,)
        assert conn.execute("SELECT status FROM document_versions").fetchone() == ("published",)
        assert conn.execute("SELECT owner_user_id FROM tenants").fetchone() == (None,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    output = io.StringIO()
    command.upgrade(
        config_for("postgresql+psycopg://unused/unused", output), "20261022_0013:head", sql=True
    )
    sql = output.getvalue()
    assert "FORCE ROW LEVEL SECURITY" in sql and "WITH CHECK" in sql
    assert "DEFAULT false NOT NULL" in sql
