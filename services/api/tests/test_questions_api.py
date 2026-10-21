from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from enterprise_ai_api.ai_providers import AnswerDraft
from enterprise_ai_api.main import create_app

ADMIN_A = UUID("00000000-0000-0000-0000-00000000000a")
ADMIN_B = UUID("00000000-0000-0000-0000-00000000000b")
MEMBER_A = UUID("00000000-0000-0000-0000-000000000001")


def headers(user_id: UUID) -> dict[str, str]:
    return {"X-Dev-User-Id": str(user_id)}


def create_workspace(
    test_client: TestClient,
    user_id: UUID,
    name: str,
    *,
    classification: str = "confidential",
) -> tuple[str, str]:
    tenant = test_client.post("/v1/tenants", json={"name": name}, headers=headers(user_id))
    tenant_id = tenant.json()["id"]
    space = test_client.post(
        f"/v1/tenants/{tenant_id}/spaces",
        json={"name": "Policies", "classification": classification},
        headers=headers(user_id),
    )
    return tenant_id, space.json()["id"]


def upload_document(
    test_client: TestClient,
    tenant_id: str,
    space_id: str,
    content: bytes,
    *,
    publish: bool,
) -> dict[str, object]:
    uploaded = test_client.post(
        f"/v1/tenants/{tenant_id}/spaces/{space_id}/documents",
        data={"title": "Remote work policy"},
        files={"file": ("remote-work.md", content, "text/markdown")},
        headers=headers(ADMIN_A),
    )
    assert uploaded.status_code == 201
    result = uploaded.json()
    if publish:
        published = test_client.post(
            f"/v1/tenants/{tenant_id}/document-versions/{result['version']['id']}/publish",
            headers=headers(ADMIN_A),
        )
        assert published.status_code == 200
    return result


def test_published_knowledge_returns_grounded_answer_and_citations(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as test_client:
        tenant_id, space_id = create_workspace(test_client, ADMIN_A, "Acme")
        upload_document(
            test_client,
            tenant_id,
            space_id,
            "员工每周可以远程办公两天。远程办公需要直属主管批准。".encode(),
            publish=True,
        )
        response = test_client.post(
            f"/v1/tenants/{tenant_id}/questions",
            json={"question": "远程办公需要谁批准？"},
            headers=headers(ADMIN_A),
        )
        audit = test_client.get(
            f"/v1/tenants/{tenant_id}/audit-events",
            headers=headers(ADMIN_A),
        )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "grounded"
    assert "直属主管批准" in result["answer"]
    assert result["citations"][0]["document_title"] == "Remote work policy"
    assert result["citations"][0]["locator"] == "paragraph:1"
    question_event = next(
        event for event in audit.json() if event["action"] == "knowledge.question_answered"
    )
    assert question_event["details"]["citation_count"] == 1
    assert "远程办公" not in str(question_event["details"])
    assert "直属主管" not in str(question_event["details"])


def test_ready_document_is_not_searchable_and_refusal_is_saved(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as test_client:
        tenant_id, space_id = create_workspace(test_client, ADMIN_A, "Acme")
        upload_document(
            test_client,
            tenant_id,
            space_id,
            b"Unpublished acquisition strategy is Project Aurora.",
            publish=False,
        )
        response = test_client.post(
            f"/v1/tenants/{tenant_id}/questions",
            json={"question": "What is the acquisition strategy?"},
            headers=headers(ADMIN_A),
        )
        history = test_client.get(
            f"/v1/tenants/{tenant_id}/questions",
            headers=headers(ADMIN_A),
        )

    assert response.status_code == 201
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["citations"] == []
    assert history.status_code == 200
    assert history.json()[0]["id"] == response.json()["id"]


def test_member_can_ask_but_history_is_private_to_each_user(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as test_client:
        tenant_id, space_id = create_workspace(test_client, ADMIN_A, "Acme")
        upload_document(test_client, tenant_id, space_id, b"Annual leave is 20 days.", publish=True)
        test_client.post(
            f"/v1/tenants/{tenant_id}/members",
            json={"user_id": str(MEMBER_A), "role": "member"},
            headers=headers(ADMIN_A),
        )
        test_client.post(
            f"/v1/tenants/{tenant_id}/spaces/{space_id}/members",
            json={"user_id": str(MEMBER_A), "role": "viewer"},
            headers=headers(ADMIN_A),
        )
        member_answer = test_client.post(
            f"/v1/tenants/{tenant_id}/questions",
            json={"question": "How many annual leave days are available?"},
            headers=headers(MEMBER_A),
        )
        member_history = test_client.get(
            f"/v1/tenants/{tenant_id}/questions", headers=headers(MEMBER_A)
        )
        admin_history = test_client.get(
            f"/v1/tenants/{tenant_id}/questions", headers=headers(ADMIN_A)
        )

    assert member_answer.status_code == 201
    assert member_answer.json()["status"] == "grounded"
    assert len(member_history.json()) == 1
    assert admin_history.json() == []


def test_space_filter_and_cross_tenant_boundary(tmp_path: Path) -> None:
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as test_client:
        tenant_a, space_a = create_workspace(test_client, ADMIN_A, "Acme")
        tenant_b, _ = create_workspace(test_client, ADMIN_B, "Globex")
        upload_document(
            test_client,
            tenant_a,
            space_a,
            b"The security code is ORCHID.",
            publish=True,
        )

        filtered = test_client.post(
            f"/v1/tenants/{tenant_a}/questions",
            json={"question": "What is the security code?", "space_ids": [tenant_b]},
            headers=headers(ADMIN_A),
        )
        cross_tenant = test_client.post(
            f"/v1/tenants/{tenant_a}/questions",
            json={"question": "What is the security code?"},
            headers=headers(ADMIN_B),
        )

    assert filtered.status_code == 201
    assert filtered.json()["status"] == "insufficient_evidence"
    assert cross_tenant.status_code == 404
    assert "ORCHID" not in cross_tenant.text


class FakeEmbeddingProvider:
    enabled = True
    external = False
    name = "fake"
    model_name = "fake-embedding-v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[1.0, 0.0] for _text in texts]


class ExternalAnswerProvider:
    external = True
    name = "external-test"
    model_name = "external-answer-v1"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, question: str, evidence: list[object]) -> AnswerDraft:
        del question, evidence
        self.calls += 1
        raise AssertionError("restricted evidence must not be sent to an external provider")


class ExternalEmbeddingProvider(FakeEmbeddingProvider):
    external = True


def test_semantic_retrieval_finds_non_lexical_match_and_encrypts_vector(
    tmp_path: Path,
) -> None:
    embedding_provider = FakeEmbeddingProvider()
    app = create_app(
        "sqlite://",
        storage_root=str(tmp_path / "uploads"),
        embedding_provider=embedding_provider,
    )
    with TestClient(app) as test_client:
        tenant_id, space_id = create_workspace(test_client, ADMIN_A, "Acme")
        uploaded = upload_document(
            test_client,
            tenant_id,
            space_id,
            b"Employees may work from home two days per week.",
            publish=True,
        )
        reindex = test_client.post(
            f"/v1/tenants/{tenant_id}/document-versions/"
            f"{uploaded['version']['id']}/semantic-index",
            headers=headers(ADMIN_A),
        )
        response = test_client.post(
            f"/v1/tenants/{tenant_id}/questions",
            json={"question": "What is the telecommuting allowance?"},
            headers=headers(ADMIN_A),
        )
        with app.state.engine.connect() as connection:
            stored_embedding = connection.execute(
                text("SELECT embedding FROM document_chunks LIMIT 1")
            ).scalar_one()

    assert response.status_code == 201
    assert reindex.status_code == 200
    assert response.json()["status"] == "grounded"
    assert response.json()["generator"] == "local"
    assert response.json()["citations"][0]["document_title"] == "Remote work policy"
    assert stored_embedding.startswith("enc:v1:")
    assert "1.0" not in stored_embedding
    assert len(embedding_provider.calls) == 3


def test_restricted_space_blocks_external_embedding_and_answer(tmp_path: Path) -> None:
    embedding_provider = ExternalEmbeddingProvider()
    answer_provider = ExternalAnswerProvider()
    app = create_app(
        "sqlite://",
        storage_root=str(tmp_path / "uploads"),
        embedding_provider=embedding_provider,
        answer_provider=answer_provider,
    )
    with TestClient(app) as test_client:
        tenant_id, space_id = create_workspace(
            test_client,
            ADMIN_A,
            "Acme",
            classification="restricted",
        )
        uploaded = upload_document(
            test_client,
            tenant_id,
            space_id,
            b"The restricted retention period is seven years.",
            publish=True,
        )
        reindex = test_client.post(
            f"/v1/tenants/{tenant_id}/document-versions/"
            f"{uploaded['version']['id']}/semantic-index",
            headers=headers(ADMIN_A),
        )
        response = test_client.post(
            f"/v1/tenants/{tenant_id}/questions",
            json={"question": "What is the restricted retention period?"},
            headers=headers(ADMIN_A),
        )

    assert response.status_code == 201
    assert response.json()["status"] == "grounded"
    assert response.json()["generator"] == "local"
    assert reindex.status_code == 409
    assert embedding_provider.calls == []
    assert answer_provider.calls == 0
