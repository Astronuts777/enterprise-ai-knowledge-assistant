from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_documents_api import ADMIN_A, ADMIN_B, MEMBER_A, create_workspace, headers
from test_questions_api import upload_document

from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import AnswerCitation, DocumentChunk


@pytest.fixture
def source_workspace(tmp_path: Path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Citation QA")
        base = f"/v1/tenants/{tenant}"
        document = upload_document(
            client,
            tenant,
            space,
            b"Introduction.\n\nRemote work requires manager approval.\n\nEnd.",
            publish=True,
        )
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
        answer = client.post(
            f"{base}/questions",
            json={"question": "Remote work approval?"},
            headers=headers(MEMBER_A),
        ).json()
        assert answer["status"] == "grounded"
        citation = answer["citations"][0]
        route = f"{base}/questions/{answer['id']}/citations/{citation['citation_number']}/source"
        yield app, client, tenant, space, document, answer, route


def test_exact_published_version_and_chunk_are_returned_not_latest_draft(source_workspace):
    _, client, tenant, _, document, answer, route = source_workspace
    draft = client.post(
        f"/v1/tenants/{tenant}/documents/{document['document']['id']}/versions",
        files={"file": ("draft.md", b"Draft secret policy.")},
        headers=headers(ADMIN_A),
    )
    assert draft.status_code == 201
    result = client.get(route, headers=headers(MEMBER_A))
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    source = result.json()
    assert source["answer_id"] == answer["id"]
    assert source["version"]["id"] == answer["citations"][0]["document_version_id"]
    assert source["version"]["version_number"] == 1
    assert source["cited_chunk_id"] == answer["citations"][0]["document_chunk_id"]
    assert len(source["chunks"]) == 3
    assert "Draft secret" not in result.text
    audit = client.get(
        f"/v1/tenants/{tenant}/audit-events?action=knowledge.citation_viewed",
        headers=headers(ADMIN_A),
    ).json()
    assert len(audit) == 1
    assert audit[0]["details"]["chunk_id"] == source["cited_chunk_id"]
    assert "manager approval" not in str(audit)


def test_answer_ownership_tenant_and_unknown_citation_are_enforced(source_workspace):
    _, client, tenant, _, _, answer, route = source_workspace
    other, _ = create_workspace(client, ADMIN_B, "Other")
    assert client.get(route).status_code == 401
    # Even an enterprise admin cannot browse another person's private question history.
    assert client.get(route, headers=headers(ADMIN_A)).status_code == 404
    assert client.get(route, headers=headers(ADMIN_B)).status_code == 404
    assert client.get(route.replace(tenant, other), headers=headers(ADMIN_B)).status_code == 404
    assert (
        client.get(route.replace(answer["id"], str(uuid4())), headers=headers(MEMBER_A)).status_code
        == 404
    )
    assert (
        client.get(
            route.replace("/citations/1/", "/citations/999/"), headers=headers(MEMBER_A)
        ).status_code
        == 404
    )
    assert (
        client.get(
            route.replace("/citations/1/", "/citations/0/"), headers=headers(MEMBER_A)
        ).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["withdraw", "supersede", "revoke_space", "suspend"])
def test_stale_citation_access_is_denied(source_workspace, change):
    _, client, tenant, space, document, _, route = source_workspace
    base = f"/v1/tenants/{tenant}"
    if change == "withdraw":
        client.post(
            f"{base}/document-versions/{document['version']['id']}/unpublish",
            headers=headers(ADMIN_A),
        )
    elif change == "supersede":
        replacement = client.post(
            f"{base}/documents/{document['document']['id']}/versions",
            files={"file": ("new.md", b"New remote work guidance.")},
            headers=headers(ADMIN_A),
        ).json()
        client.post(
            f"{base}/document-versions/{replacement['id']}/publish", headers=headers(ADMIN_A)
        )
    elif change == "revoke_space":
        client.delete(f"{base}/spaces/{space}/members/{MEMBER_A}", headers=headers(ADMIN_A))
    else:
        client.patch(
            f"{base}/members/{MEMBER_A}/status", json={"active": False}, headers=headers(ADMIN_A)
        )
    response = client.get(route, headers=headers(MEMBER_A))
    assert response.status_code == 404
    assert "manager approval" not in response.text


def test_inconsistent_chunk_cannot_redirect_to_other_version(source_workspace):
    app, client, tenant, space, _, answer, route = source_workspace
    other = upload_document(client, tenant, space, b"Unrelated source.", publish=True)
    with app.state.session_factory() as session:
        other_chunk = session.scalar(
            select(DocumentChunk).where(
                DocumentChunk.document_version_id == UUID(other["version"]["id"]),
            )
        )
        citation = session.scalar(
            select(AnswerCitation).where(
                AnswerCitation.answer_id == UUID(answer["id"]),
                AnswerCitation.citation_number == 1,
            )
        )
        citation.document_chunk_id = other_chunk.id
        session.commit()
    response = client.get(route, headers=headers(MEMBER_A))
    assert response.status_code == 404
    assert "Unrelated source" not in response.text
