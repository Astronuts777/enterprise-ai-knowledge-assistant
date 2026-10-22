from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import update
from test_documents_api import ADMIN_A, ADMIN_B, MEMBER_A, create_workspace, headers

from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentVersion


def upload(client, tenant, space, content=b"Remote work requires manager approval."):
    result = client.post(
        f"/v1/tenants/{tenant}/spaces/{space}/documents",
        files={"file": ("policy.md", content)},
        headers=headers(ADMIN_A),
    )
    assert result.status_code == 201
    return result.json()


def test_withdrawal_during_generation_discards_stale_answer(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        item = upload(client, tenant, space)
        version = item["version"]["id"]
        base = f"/v1/tenants/{tenant}"
        client.post(f"{base}/document-versions/{version}/publish", headers=headers(ADMIN_A))
        original = app.state.answer_provider

        class WithdrawingProvider:
            name = "test"
            model_name = None
            external = False

            def generate(self, *, question, evidence):
                draft = original.generate(question=question, evidence=evidence)
                assert draft.evidence
                with app.state.session_factory() as session:
                    session.execute(
                        update(DocumentVersion)
                        .where(
                            DocumentVersion.id == UUID(version),
                        )
                        .values(status="ready", published_at=None)
                    )
                    session.commit()
                return draft

        app.state.answer_provider = WithdrawingProvider()
        response = client.post(
            f"{base}/questions",
            json={"question": "Remote work approval?"},
            headers=headers(ADMIN_A),
        )
        assert response.status_code == 409
        assert "manager approval" not in response.text
        assert client.get(f"{base}/questions", headers=headers(ADMIN_A)).json() == []


def test_withdraw_republish_and_history_source_checks(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        item = upload(client, tenant, space)
        document, version = item["document"]["id"], item["version"]["id"]
        base = f"/v1/tenants/{tenant}"
        publication = f"{base}/document-versions/{version}"
        client.post(
            f"{base}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )
        client.post(
            f"{base}/spaces/{space}/members",
            json={"user_id": str(MEMBER_A), "role": "viewer"},
            headers=headers(ADMIN_A),
        )
        assert client.post(f"{publication}/publish", headers=headers(ADMIN_A)).status_code == 200
        for actor in (ADMIN_A, MEMBER_A):
            answer = client.post(
                f"{base}/questions",
                json={"question": "Remote work approval?"},
                headers=headers(actor),
            )
            assert answer.json()["status"] == "grounded"
            assert len(client.get(f"{base}/questions", headers=headers(actor)).json()) == 1
        objects_before = {
            p: p.read_bytes() for p in (tmp_path / "uploads").rglob("*") if p.is_file()
        }

        withdrawn = client.post(f"{publication}/unpublish", headers=headers(ADMIN_A))
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "ready"
        assert withdrawn.json()["published_at"] is None
        assert client.post(f"{publication}/unpublish", headers=headers(ADMIN_A)).status_code == 409
        assert (
            client.get(f"{base}/spaces/{space}/documents", headers=headers(MEMBER_A)).json() == []
        )
        for suffix in ("", "/content", f"/content?version_id={version}"):
            assert (
                client.get(
                    f"{base}/documents/{document}{suffix}", headers=headers(MEMBER_A)
                ).status_code
                == 404
            )
        for actor in (ADMIN_A, MEMBER_A):
            assert client.get(f"{base}/questions", headers=headers(actor)).json() == []
        assert client.get(
            f"{base}/documents/{document}/content?version_id={version}", headers=headers(ADMIN_A)
        ).json()["chunks"]
        assert (
            client.get(f"{base}/knowledge-status", headers=headers(ADMIN_A)).json()[
                "published_chunks"
            ]
            == 0
        )
        refusal = client.post(
            f"{base}/questions",
            json={"question": "Remote work approval?"},
            headers=headers(MEMBER_A),
        ).json()
        assert refusal["status"] == "insufficient_evidence"
        assert refusal["citations"] == []
        assert {p: p.read_bytes() for p in objects_before} == objects_before
        audit = client.get(
            f"{base}/audit-events?action=document.unpublished", headers=headers(ADMIN_A)
        ).json()
        assert len(audit) == 1
        assert audit[0]["details"]["version_number"] == 1
        assert "Remote work" not in str(audit)
        assert client.post(f"{publication}/publish", headers=headers(ADMIN_A)).status_code == 200
        assert (
            client.get(
                f"{base}/documents/{document}/content", headers=headers(MEMBER_A)
            ).status_code
            == 200
        )
        assert len(client.get(f"{base}/questions", headers=headers(ADMIN_A)).json()) == 1


def test_version_management_works_with_newer_draft_and_superseded_sources(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        base = f"/v1/tenants/{tenant}"
        item = upload(client, tenant, space)
        document, first = item["document"]["id"], item["version"]["id"]
        client.post(f"{base}/document-versions/{first}/publish", headers=headers(ADMIN_A))
        answer = client.post(
            f"{base}/questions",
            json={"question": "Remote work approval?"},
            headers=headers(ADMIN_A),
        ).json()
        assert answer["status"] == "grounded"
        second = client.post(
            f"{base}/documents/{document}/versions",
            files={"file": ("new.md", b"Remote work no longer needs approval.")},
            headers=headers(ADMIN_A),
        ).json()["id"]
        detail = client.get(f"{base}/documents/{document}", headers=headers(ADMIN_A)).json()
        assert [v["status"] for v in detail["versions"]] == ["ready", "published"]
        summary = client.get(f"{base}/spaces/{space}/documents", headers=headers(ADMIN_A)).json()[0]
        assert summary["latest_version"]["id"] == second
        assert summary["published_version"]["id"] == first
        assert (
            client.get(
                f"{base}/documents/{document}/content?version_id={first}", headers=headers(ADMIN_A)
            ).json()["version"]["id"]
            == first
        )
        other = upload(client, tenant, space, b"Another document.")
        assert (
            client.get(
                f"{base}/documents/{document}/content?version_id={other['version']['id']}",
                headers=headers(ADMIN_A),
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{base}/document-versions/{second}/publish", headers=headers(ADMIN_A)
            ).status_code
            == 200
        )
        assert client.get(f"{base}/questions", headers=headers(ADMIN_A)).json() == []
        assert (
            client.post(
                f"{base}/document-versions/{first}/unpublish", headers=headers(ADMIN_A)
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"{base}/document-versions/{first}/publish", headers=headers(ADMIN_A)
            ).status_code
            == 200
        )
        detail = client.get(f"{base}/documents/{document}", headers=headers(ADMIN_A)).json()
        assert [v["status"] for v in detail["versions"]] == ["superseded", "published"]
        assert (
            client.post(
                f"{base}/document-versions/{first}/unpublish", headers=headers(ADMIN_A)
            ).status_code
            == 200
        )


def test_publication_mutations_reject_unauthorized_users(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Acme")
        other_tenant, _ = create_workspace(client, ADMIN_B, "Other")
        item = upload(client, tenant, space)
        base = f"/v1/tenants/{tenant}"
        version = item["version"]["id"]
        client.post(
            f"{base}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )
        client.post(
            f"{base}/spaces/{space}/members",
            json={"user_id": str(MEMBER_A), "role": "viewer"},
            headers=headers(ADMIN_A),
        )
        client.post(f"{base}/document-versions/{version}/publish", headers=headers(ADMIN_A))
        for operation in ("publish", "unpublish"):
            route = f"{base}/document-versions/{version}/{operation}"
            assert client.post(route).status_code == 401
            assert client.post(route, headers=headers(MEMBER_A)).status_code == 403
            assert client.post(route, headers=headers(ADMIN_B)).status_code == 404
            assert (
                client.post(
                    route.replace(tenant, other_tenant), headers=headers(ADMIN_B)
                ).status_code
                == 404
            )
        client.post(
            f"{base}/members",
            json={"user_id": str(MEMBER_A), "role": "knowledge_admin"},
            headers=headers(ADMIN_A),
        )
        # A knowledge admin with viewer-only space access still cannot publish/withdraw.
        assert (
            client.post(
                f"{base}/document-versions/{version}/unpublish", headers=headers(MEMBER_A)
            ).status_code
            == 404
        )
        client.post(
            f"{base}/spaces/{space}/members",
            json={"user_id": str(MEMBER_A), "role": "manager"},
            headers=headers(ADMIN_A),
        )
        assert (
            client.post(
                f"{base}/document-versions/{version}/unpublish", headers=headers(MEMBER_A)
            ).status_code
            == 200
        )
        client.patch(
            f"{base}/members/{MEMBER_A}/status", json={"active": False}, headers=headers(ADMIN_A)
        )
        assert (
            client.post(
                f"{base}/document-versions/{version}/publish", headers=headers(MEMBER_A)
            ).status_code
            == 404
        )
