"""Full-corpus recall without retaining the whole decrypted corpus in memory."""

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Text, cast, insert, literal, select, text, update
from sqlalchemy.engine import make_url
from test_questions_api import ADMIN_A, ADMIN_B, create_workspace, headers, upload_document

from enterprise_ai_api import retrieval
from enterprise_ai_api.database import set_tenant_context
from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentChunk, DocumentVersion


@pytest.fixture
def corpus(tmp_path):
    # Optional integration run against a disposable, already migrated database.
    # No schema creation, dropping, or truncation is performed by this fixture.
    url = os.getenv("RETRIEVAL_TEST_DATABASE_URL", "sqlite://")
    if url != "sqlite://" and make_url(url).database != "enterprise_retrieval_test":
        raise ValueError("Use an isolated enterprise_retrieval_test database")
    app = create_app(url, storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Streaming QA")
        doc = upload_document(client, tenant, space, b"Archive ZX-987 approval.", publish=True)
        version_id = UUID(doc["version"]["id"])
        with app.state.session_factory() as session:
            from enterprise_ai_api.database import set_tenant_context

            set_tenant_context(session, UUID(tenant))
            first = session.scalar(
                select(DocumentChunk).where(
                    DocumentChunk.document_version_id == version_id,
                )
            )
            first.created_at = datetime(2020, 1, 1, tzinfo=UTC)
            first.embedding = "[1, 0]"
            first.embedding_model = "test-vector"
            first_id = first.id
            session.execute(
                insert(DocumentChunk),
                [
                    dict(
                        id=uuid4(),
                        tenant_id=UUID(tenant),
                        document_version_id=version_id,
                        position=i,
                        locator=f"paragraph:{i}",
                        text="Routine unrelated material.",
                        embedding="[0, 1]",
                        embedding_model="test-vector",
                        created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                    for i in range(2, 5012)
                ],
            )
            session.commit()
        yield app, client, UUID(tenant), UUID(space), version_id, first_id


@pytest.mark.parametrize("mode", ["lexical", "semantic", "filename"])
def test_old_source_beyond_5000_and_adjacent_context_are_found(corpus, mode):
    app, _, tenant, space, version_id, first_id = corpus
    options = {"question": "ZX-987"}
    if mode == "semantic":
        options = dict(question="异地工作", query_embedding=[1, 0], embedding_model="test-vector")
    elif mode == "filename":
        options = dict(question="remote-work.md")
    with app.state.session_factory() as session:
        from enterprise_ai_api.database import set_tenant_context

        set_tenant_context(session, tenant)
        result = retrieval.search_published_knowledge(
            session,
            tenant_id=tenant,
            space_ids=[space],
            **options,
        )
        assert result[0].chunk.id == first_id
        assert result[0].version.id == version_id
        assert [item.chunk.position for item in result] == [1, 2]
        if mode == "semantic":
            assert result[0].lexical_score == 0
            assert result[0].semantic_score == 1


def test_large_corpus_api_answer_and_source_navigation(corpus):
    _, client, tenant, _, _, first_id = corpus
    answer = client.post(
        f"/v1/tenants/{tenant}/questions",
        json={"question": "ZX-987"},
        headers=headers(ADMIN_A),
    )
    assert answer.status_code == 201
    payload = answer.json()
    assert payload["status"] == "grounded"
    assert payload["citations"][0]["document_chunk_id"] == str(first_id)
    source = client.get(
        f"/v1/tenants/{tenant}/questions/{payload['id']}/citations/1/source",
        headers=headers(ADMIN_A),
    )
    assert source.status_code == 200
    assert source.json()["cited_chunk_id"] == str(first_id)


def test_all_matching_chunks_are_scored_with_bounded_retention(corpus, monkeypatch):
    app, _, tenant, space, _, _ = corpus
    sizes = []
    excerpts = []
    original = retrieval._score
    original_excerpt = retrieval._best_excerpt
    with app.state.session_factory() as session:
        from enterprise_ai_api.database import set_tenant_context

        set_tenant_context(session, tenant)

        def tracked(*args):
            sizes.append(len(session.identity_map))
            return original(*args)

        def tracked_excerpt(*args):
            excerpts.append(1)
            return original_excerpt(*args)

        monkeypatch.setattr(retrieval, "_score", tracked)
        monkeypatch.setattr(retrieval, "_best_excerpt", tracked_excerpt)
        result = retrieval.search_published_knowledge(
            session,
            tenant_id=tenant,
            space_ids=[space],
            question="Routine unrelated material",
        )
        assert len(sizes) == 5012  # 5,011 bodies plus one metadata comparison
        # Scalar scoring creates no ORM objects or excerpts, even with all hits.
        assert max(sizes) == 0
        assert len(excerpts) == len(result) <= retrieval.MAX_CITATIONS
        assert len(result) <= retrieval.MAX_CITATIONS
        assert [item.chunk.position for item in result[:4]] == [2, 3, 4, 5]


def test_forbidden_and_unpublished_ciphertext_is_never_loaded(corpus):
    app, client, tenant, space, _, _ = corpus
    hidden_space = client.post(
        f"/v1/tenants/{tenant}/spaces",
        json={"name": "Hidden"},
        headers=headers(ADMIN_A),
    ).json()["id"]
    other_tenant, other_space = create_workspace(client, ADMIN_B, "Other tenant")
    # upload_document uses ADMIN_A, so create the other tenant's document with ADMIN_B.
    other_doc = client.post(
        f"/v1/tenants/{other_tenant}/spaces/{other_space}/documents",
        data={"title": "Forbidden"},
        files={"file": ("hidden.md", b"ZX-987")},
        headers=headers(ADMIN_B),
    ).json()
    client.post(
        f"/v1/tenants/{other_tenant}/document-versions/{other_doc['version']['id']}/publish",
        headers=headers(ADMIN_B),
    ).raise_for_status()
    hidden = upload_document(client, str(tenant), hidden_space, b"ZX-987 hidden", publish=True)
    draft = upload_document(client, str(tenant), str(space), b"ZX-987 draft", publish=False)
    for scoped_tenant, doc in [(tenant, hidden), (tenant, draft), (UUID(other_tenant), other_doc)]:
        with app.state.session_factory() as session:
            from enterprise_ai_api.database import set_tenant_context

            set_tenant_context(session, scoped_tenant)
            # Deliberately invalid encrypted bytes: any unauthorized loading
            # (including neighbor expansion) would raise during ORM decryption.
            session.execute(
                text(
                    "UPDATE document_chunks SET text = 'enc:v1:invalid' "
                    "WHERE document_version_id = :version"
                ),
                {
                    "version": UUID(doc["version"]["id"]).hex
                    if session.bind.dialect.name == "sqlite"
                    else doc["version"]["id"]
                },
            )
            session.commit()
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        assert retrieval.search_published_knowledge(
            session,
            tenant_id=tenant,
            space_ids=[space],
            question="ZX-987",
        )
        assert (
            retrieval.search_published_knowledge(
                session,
                tenant_id=tenant,
                space_ids=[],
                question="ZX-987",
            )
            == []
        )


def test_streaming_top_k_matches_full_sort_and_is_batch_independent(corpus, monkeypatch):
    app, client, tenant, space, _, _ = corpus
    for index in range(6):
        upload_document(
            client,
            str(tenant),
            str(space),
            f"ZX-987 {index}.\n\nZX-987 approval {index}.".encode(),
            publish=True,
        )
    baseline = None
    for batch_size in [1, 97, 256]:
        monkeypatch.setattr(retrieval, "RETRIEVAL_BATCH_SIZE", batch_size)
        with app.state.session_factory() as session:
            from enterprise_ai_api.database import set_tenant_context

            set_tenant_context(session, tenant)
            result = retrieval.search_published_knowledge(
                session,
                tenant_id=tenant,
                space_ids=[space],
                question="ZX-987",
            )
            ids = [item.chunk.id for item in result]
            assert len({item.document.id for item in result[:4]}) == 4
            assert len(ids) == len(set(ids))
            assert len(ids) <= 8
            if baseline is None:
                baseline = ids
            assert ids == baseline
            # All matching bodies have equal saturated lexical scores. Their
            # global full-sort order is title, position, UUID, with distinct docs.
            chunks = list(
                session.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.tenant_id == tenant,
                        DocumentChunk.position == 1,
                    )
                )
            )
            expected = sorted(chunks, key=lambda chunk: str(chunk.id))[:4]
            assert ids[:4] == [chunk.id for chunk in expected]


def test_interrupted_scan_closes_cursor_without_returning_partial_answer(corpus, monkeypatch):
    app, _, tenant, space, _, _ = corpus
    calls = 0
    cursors = []
    original_score = retrieval._score
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        execute = session.execute

        def capture(statement, *args, **kwargs):
            result = execute(statement, *args, **kwargs)
            cursors.append(result)
            return result

        def fail_after_first_batch(*args):
            nonlocal calls
            calls += 1
            if calls > retrieval.RETRIEVAL_BATCH_SIZE + 1:
                raise RuntimeError("Synthetic interrupted scan")
            return original_score(*args)

        monkeypatch.setattr(session, "execute", capture)
        monkeypatch.setattr(retrieval, "_score", fail_after_first_batch)
        with pytest.raises(RuntimeError, match="Synthetic interrupted scan"):
            retrieval.search_published_knowledge(
                session,
                tenant_id=tenant,
                space_ids=[space],
                question="ZX-987",
            )
        assert len(cursors) == 1
        assert cursors[0].closed


@pytest.mark.parametrize("semantic", [False, True])
def test_unneeded_vectors_are_not_decrypted_even_for_selected_evidence(corpus, semantic):
    app, _, tenant, space, _, first_id = corpus
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        # First chunk is an anchor, second is context, the others are scanned.
        # Every vector is corrupted but belongs to a different model. Loading
        # these encrypted fields anywhere would fail, not just affect ranking.
        session.execute(update(DocumentChunk).where(DocumentChunk.tenant_id == tenant).values(
            embedding=literal("enc:v1:aW52YWxpZA==", type_=Text),
        ))
        session.commit()
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        result = retrieval.search_published_knowledge(
            session, tenant_id=tenant, space_ids=[space], question="ZX-987",
            query_embedding=[1, 0] if semantic else None,
            embedding_model="different-model" if semantic else None,
        )
        assert result[0].chunk.id == first_id
        assert [item.chunk.position for item in result] == [1, 2]
        assert all(item.semantic_score is None for item in result)


def test_matching_corrupt_vector_still_fails_closed(corpus):
    app, _, tenant, space, _, _ = corpus
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        session.execute(update(DocumentChunk).where(DocumentChunk.tenant_id == tenant).values(
            embedding=literal("enc:v1:aW52YWxpZA==", type_=Text),
        ))
        session.commit()
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        with pytest.raises(ValueError, match="Encrypted payload header"):
            retrieval.search_published_knowledge(
                session, tenant_id=tenant, space_ids=[space], question="ZX-987",
                query_embedding=[1, 0], embedding_model="test-vector",
            )


def test_withdrawal_between_scoring_and_hydration_returns_no_stale_evidence(corpus, monkeypatch):
    app, _, tenant, space, version_id, _ = corpus
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        execute = session.execute
        calls = 0

        def withdraw_before_hydration(statement, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                execute(update(DocumentVersion).where(
                    DocumentVersion.id == version_id,
                ).values(status="ready"))
                session.commit()
                set_tenant_context(session, tenant)
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", withdraw_before_hydration)
        assert retrieval.search_published_knowledge(
            session, tenant_id=tenant, space_ids=[space], question="ZX-987",
        ) == []


def test_retrieval_does_not_rewrite_encrypted_storage(corpus):
    app, _, tenant, space, _, first_id = corpus
    with app.state.session_factory() as session:
        set_tenant_context(session, tenant)
        ciphertext = select(
            cast(DocumentChunk.text, Text), cast(DocumentChunk.embedding, Text),
        ).where(DocumentChunk.id == first_id)
        before = session.execute(ciphertext).one()
        assert all(value.startswith("enc:v1:") for value in before)
        assert "Archive" not in before[0]
        retrieval.search_published_knowledge(
            session, tenant_id=tenant, space_ids=[space], question="ZX-987",
            query_embedding=[1, 0], embedding_model="test-vector",
        )
        session.commit()
        set_tenant_context(session, tenant)
        assert session.execute(ciphertext).one() == before
