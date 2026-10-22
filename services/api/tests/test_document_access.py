"""Isolated synthetic enterprises only; no production identities or documents."""

import io
import sqlite3
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, text, update
from test_documents_api import create_workspace, headers
from test_worker_federation_migration import config_for

from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentGrant, Tenant


@pytest.fixture
def setup(tmp_path):
    owner, admin, employee, outsider = (uuid4() for _ in range(4))
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, owner, "Isolated ACL test company")
        base = f"/v1/tenants/{tenant}"
        for actor, role in ((admin, "enterprise_admin"), (employee, "member")):
            assert client.post(base + "/members", headers=headers(owner), json={
                "user_id": str(actor), "role": role,
            }).status_code == 201
        other, _ = create_workspace(client, outsider, "Other isolated company")
        item = client.post(base + f"/spaces/{space}/documents", headers=headers(owner),
                           data={"access_mode": "restricted", "title": "Secret approval"},
                           files={"file": ("policy.txt", b"Secret approval needs Director Alice.")})
        assert item.status_code == 201, item.text
        doc, version = item.json()["document"]["id"], item.json()["version"]["id"]
        assert client.post(base + f"/document-versions/{version}/publish",
                           headers=headers(owner)).status_code == 200
        yield client, app, base, space, doc, version, owner, admin, employee, outsider, other


def test_owner_is_per_tenant_and_not_an_admin_account_alias(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    assert c.get(base, headers=headers(owner)).json()["owner_user_id"] == str(owner)
    assert c.get(f"/v1/tenants/{other}", headers=headers(outsider)).json()[
        "owner_user_id"] == str(outsider)
    assert c.post(base + "/members", headers=headers(admin), json={
        "user_id": str(owner), "role": "member",
    }).status_code == 409
    assert c.patch(base + f"/members/{owner}/status", headers=headers(admin),
                   json={"active": False}).status_code == 409
    # Existing enterprises are intentionally unclaimed, not first-admin-wins.
    with app.state.session_factory() as s:
        s.execute(update(Tenant).where(Tenant.id == UUID(base.split("/")[-1]))
                  .values(owner_user_id=None))
        s.commit()
    assert c.put(base + f"/documents/{doc}/access", headers=headers(owner),
                 json={"access_mode": "space"}).status_code == 403


def test_directory_encrypted_profiles_and_tenant_boundary(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    profile = {"display_name": "测试员工乙", "department": "研发部"}
    assert c.patch(base + f"/employees/{employee}", headers=headers(employee),
                   json=profile).status_code == 200
    rows = c.get(base + "/employees", headers=headers(employee)).json()
    assert len(rows) == 3
    assert sum(e["is_owner"] for e in rows) == 1
    assert all("email" not in e for e in rows)
    assert len(c.get(base + "/employees?limit=1", headers=headers(employee)).json()) == 1
    assert len(c.get(base + "/employees?offset=1", headers=headers(employee)).json()) == 2
    assert c.get(base + "/employees", headers=headers(outsider)).status_code == 404
    assert c.patch(base + f"/employees/{owner}", headers=headers(employee),
                   json=profile).status_code == 403
    assert c.patch(base + f"/employees/{employee}", headers=headers(employee),
                   json={**profile, "role": "enterprise_admin"}).status_code == 422
    with app.state.engine.connect() as conn:
        value = conn.execute(text("SELECT display_name, department FROM memberships "
                                  "WHERE display_name IS NOT NULL")).one()
        assert all(v.startswith("enc:v1:") for v in value)
        assert "测试员工乙" not in str(value)


def test_admin_cannot_bypass_restricted_document_or_decrypt_it(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    for actor in (admin, employee, outsider):
        for suffix in ("", "/content"):
            assert c.get(base + f"/documents/{doc}{suffix}",
                         headers=headers(actor)).status_code == 404
        assert c.get(base + f"/document-versions/{version}/chunks",
                     headers=headers(actor)).status_code == 404
        assert c.put(base + f"/documents/{doc}/grants/{employee}",
                     headers=headers(actor), json={"role": "editor"}).status_code in (403, 404)
    assert c.get(base + f"/spaces/{space}/documents", headers=headers(admin)).json() == []
    # Deliberately corrupt inaccessible ciphertext. SQL filtering must precede decryption.
    with app.state.engine.begin() as conn:
        conn.execute(text("UPDATE document_chunks SET text='enc:v1:invalid'"))
    answer = c.post(base + "/questions", headers=headers(admin),
                    json={"question": "Secret approval?"})
    assert answer.status_code == 201
    assert answer.json()["citations"] == []
    assert c.get(base + "/knowledge-status", headers=headers(admin)).json()[
        "published_chunks"] == 0


def test_reader_editor_and_revocation_cover_all_read_paths(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    access = base + f"/documents/{doc}"
    grant = access + f"/grants/{employee}"
    # A different inherited document must NOT leak via the newly visible space.
    sibling = c.post(base + f"/spaces/{space}/documents", headers=headers(owner),
                     files={"file": ("sibling.txt", b"Another confidential project.")}).json()
    c.post(base + f"/document-versions/{sibling['version']['id']}/publish",
           headers=headers(owner))
    assert c.put(grant, headers=headers(owner), json={"role": "reader"}).status_code == 200
    assert c.get(access + "/content", headers=headers(employee)).status_code == 200
    listed = c.get(base + f"/spaces/{space}/documents", headers=headers(employee)).json()
    assert [d["document"]["id"] for d in listed] == [doc]
    assert not listed[0]["can_edit"] and not listed[0]["can_publish"]
    assert c.get(base + f"/documents/{sibling['document']['id']}/content",
                 headers=headers(employee)).status_code == 404
    assert c.get(base + "/knowledge-status", headers=headers(employee)).json()[
        "published_chunks"] == 1
    assert c.post(access + "/versions", headers=headers(employee),
                  files={"file": ("new.txt", b"new text")}).status_code == 404
    answer = c.post(base + "/questions", headers=headers(employee),
                    json={"question": "Secret approval?"}).json()
    assert answer["status"] == "grounded"
    source = base + f"/questions/{answer['id']}/citations/1/source"
    assert c.get(source, headers=headers(employee)).status_code == 200
    assert c.put(grant, headers=headers(owner), json={"role": "editor"}).status_code == 200
    draft = c.post(access + "/versions", headers=headers(employee),
                   files={"file": ("new.txt", b"Secret approval now needs Director Bob.")})
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    version_path = base + f"/document-versions/{draft_id}"
    assert c.get(version_path + "/chunks", headers=headers(employee)).status_code == 200
    assert c.get(access, headers=headers(employee)).json()["can_edit"]
    for operation in ("publish", "unpublish", "semantic-index"):
        assert c.post(version_path + "/" + operation,
                      headers=headers(employee)).status_code == 403
    assert c.put(access + "/access", headers=headers(employee),
                 json={"access_mode": "space"}).status_code == 403
    assert c.put(access + f"/grants/{admin}", headers=headers(employee),
                 json={"role": "reader"}).status_code == 403
    assert c.put(access + f"/grants/{outsider}", headers=headers(owner),
                 json={"role": "reader"}).status_code == 409
    assert c.delete(grant, headers=headers(owner)).status_code == 204
    for endpoint in (access, access + "/content", version_path + "/chunks", source):
        assert c.get(endpoint, headers=headers(employee)).status_code == 404
    assert c.get(base + "/questions", headers=headers(employee)).json() == []
    assert c.get(base + "/knowledge-status", headers=headers(employee)).json()[
        "published_chunks"] == 0
    assert c.get(base + "/spaces", headers=headers(employee)).json() == []


def test_revoke_during_generation_discards_answer(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    c.put(base + f"/documents/{doc}/grants/{employee}",
          headers=headers(owner), json={"role": "reader"})
    original = app.state.answer_provider

    class RevokingProvider:
        name = "test"
        model_name = None
        external = False

        def generate(self, *, question, evidence):
            result = original.generate(question=question, evidence=evidence)
            assert evidence
            with app.state.session_factory() as s:
                s.execute(delete(DocumentGrant))
                s.commit()
            return result

    app.state.answer_provider = RevokingProvider()
    response = c.post(base + "/questions", headers=headers(employee),
                      json={"question": "Secret approval?"})
    assert response.status_code == 409
    assert "Director Alice" not in response.text


def test_access_mode_clears_stale_grants(setup):
    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    access = base + f"/documents/{doc}/access"
    c.put(base + f"/documents/{doc}/grants/{employee}",
          headers=headers(owner), json={"role": "reader"})
    assert c.put(access, headers=headers(owner), json={"access_mode": "space"}).status_code == 200
    assert c.get(access, headers=headers(owner)).json()["grants"] == []
    assert c.get(base + f"/documents/{doc}/content", headers=headers(admin)).status_code == 200
    assert c.put(access, headers=headers(owner),
                 json={"access_mode": "restricted"}).status_code == 200
    assert c.get(base + f"/documents/{doc}/content", headers=headers(employee)).status_code == 404


def test_migration_does_not_promote_existing_admin(tmp_path):
    path = tmp_path / "legacy.db"
    cfg = config_for(f"sqlite:///{path}")
    command.upgrade(cfg, "20261022_0012")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tenants (id, name, status) VALUES ('a','Legacy','active')")
        conn.execute("INSERT INTO users (id, external_subject, status) VALUES ('b','b','active')")
        conn.execute("INSERT INTO memberships (tenant_id,user_id,role) "
                     "VALUES ('a','b','enterprise_admin')")
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT owner_user_id FROM tenants").fetchone() == (None,)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    output = io.StringIO()
    command.upgrade(config_for("postgresql+psycopg://unused/unused", output),
                    "20261022_0012:head", sql=True)
    sql = output.getvalue()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "WITH CHECK" in sql
    assert "UPDATE tenants" not in sql

def test_async_editor_job_rechecks_revoked_document_grant(setup):
    from enterprise_ai_api.ingestion import process_one

    c, app, base, space, doc, version, owner, admin, employee, outsider, other = setup
    app.state.async_ingestion = True
    grant = base + f"/documents/{doc}/grants/{employee}"
    c.put(grant, headers=headers(owner), json={"role": "editor"})
    response = c.post(base + f"/documents/{doc}/versions", headers=headers(employee),
                      files={"file": ("queued.txt", b"Secret approval queued version")})
    assert response.status_code == 201
    job = base + f"/document-versions/{response.json()['id']}/job"
    assert c.get(job, headers=headers(employee)).status_code == 200
    assert c.delete(grant, headers=headers(owner)).status_code == 204
    assert c.get(job, headers=headers(employee)).status_code == 404
    assert c.post(job + "/retry", headers=headers(employee)).status_code == 404
    assert process_one(app.state.session_factory, app.state.object_storage,
                       app.state.file_scanner, app.state.embedding_provider,
                       UUID(base.split("/")[-1]))
    result = c.get(job, headers=headers(owner)).json()
    assert result["status"] == "failed"
