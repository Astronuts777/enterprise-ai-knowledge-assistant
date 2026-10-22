"""Tenant-scoped hybrid retrieval, grounded generation, and history endpoints."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from enterprise_ai_api.ai_providers import (
    AIProviderError,
    EmbeddingProvider,
    EvidenceAnswerProvider,
)
from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.document_access import document_access_predicate
from enterprise_ai_api.models import (
    AnswerCitation,
    Document,
    DocumentChunk,
    DocumentVersion,
    KnowledgeAnswer,
    Space,
)
from enterprise_ai_api.retrieval import search_published_knowledge
from enterprise_ai_api.schemas import (
    AnswerCitationResponse,
    CitationSourceResponse,
    KnowledgeAnswerResponse,
    KnowledgeQuestionCreate,
    KnowledgeStatusResponse,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    accessible_space_ids,
    authorize_space_access,
    authorize_tenant_action,
)
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
logger = logging.getLogger(__name__)
DatabaseSession = Annotated[Session, Depends(get_session)]


def get_answer_provider(request: Request) -> EvidenceAnswerProvider:
    return request.app.state.answer_provider


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    return request.app.state.embedding_provider


AnswerProvider = Annotated[EvidenceAnswerProvider, Depends(get_answer_provider)]
Embedding = Annotated[EmbeddingProvider, Depends(get_embedding_provider)]


@router.get("/tenants/{tenant_id}/knowledge-status", response_model=KnowledgeStatusResponse)
def knowledge_status(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    request: Request,
    answer_provider: AnswerProvider,
    embedding_provider: Embedding,
    space_id: UUID | None = None,
) -> dict[str, object]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    allowed = accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id)
    scoped = [value for value in allowed if space_id is None or value == space_id]
    restricted = (
        bool(
            session.scalar(
                select(Space.id)
                .where(
                    Space.id.in_(scoped),
                    Space.classification == "restricted",
                )
                .limit(1)
            )
        )
        and not request.app.state.external_ai_allowed_for_restricted
    )
    embedding_enabled = embedding_provider.enabled and not (
        restricted and embedding_provider.external
    )
    generation_enabled = answer_provider.name != "local" and not (
        restricted and answer_provider.external
    )
    total, indexed = session.execute(
        select(
            func.count(DocumentChunk.id),
            func.sum(
                case(
                    (
                        (DocumentChunk.embedding.is_not(None))
                        & (DocumentChunk.embedding_model == embedding_provider.model_name),
                        1,
                    ),
                    else_=0,
                )
            ),
        )
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.tenant_id == tenant_id,
            Document.space_id.in_(scoped),
            DocumentVersion.status == "published",
            DocumentVersion.tenant_id == tenant_id,
            DocumentChunk.tenant_id == tenant_id,
            document_access_predicate(session, tenant_id, user_id),
        )
    ).one()
    return {
        "semantic_enabled": embedding_enabled,
        "generation_enabled": generation_enabled,
        "restricted_by_policy": restricted
        and (embedding_provider.external or answer_provider.external),
        "published_chunks": total,
        "indexed_chunks": (indexed or 0) if embedding_enabled else 0,
        "embedding_model": embedding_provider.model_name if embedding_enabled else None,
        "answer_model": answer_provider.model_name if generation_enabled else None,
    }


def _response(session: Session, answer: KnowledgeAnswer) -> KnowledgeAnswerResponse:
    citations = list(
        session.scalars(
            select(AnswerCitation)
            .where(AnswerCitation.answer_id == answer.id)
            .order_by(AnswerCitation.citation_number)
        )
    )
    return KnowledgeAnswerResponse(
        id=answer.id,
        tenant_id=answer.tenant_id,
        created_by=answer.created_by,
        question=answer.question,
        answer=answer.answer,
        status=answer.status,
        generator=answer.generator,
        model_name=answer.model_name,
        citations=[AnswerCitationResponse.model_validate(citation) for citation in citations],
        created_at=answer.created_at,
    )


def _sources_visible(
    session: Session, answer_id: UUID, allowed_spaces: set[UUID], tenant_id: UUID, user_id: UUID
) -> bool:
    sources = session.execute(
        select(
            Document.space_id,
            DocumentVersion.status,
            document_access_predicate(session, tenant_id, user_id),
        )
        .select_from(AnswerCitation)
        .outerjoin(Document, AnswerCitation.document_id == Document.id)
        .outerjoin(DocumentVersion, AnswerCitation.document_version_id == DocumentVersion.id)
        .where(AnswerCitation.answer_id == answer_id)
    ).all()
    return all(
        space in allowed_spaces and state == "published" and visible
        for space, state, visible in sources
    )


@router.get(
    "/tenants/{tenant_id}/questions/{answer_id}/citations/{citation_number}/source",
    response_model=CitationSourceResponse,
)
def read_citation_source(
    tenant_id: UUID,
    answer_id: UUID,
    citation_number: Annotated[int, Path(ge=1)],
    user_id: CurrentUserId,
    session: DatabaseSession,
    response: Response,
) -> CitationSourceResponse:
    """Resolve an owned answer's immutable source IDs, never the latest draft."""
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    answer = session.get(KnowledgeAnswer, answer_id)
    if answer is None or answer.tenant_id != tenant_id or answer.created_by != user_id:
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    allowed = set(accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id))
    if not _sources_visible(session, answer_id, allowed, tenant_id, user_id):
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    citation = session.scalar(
        select(AnswerCitation).where(
            AnswerCitation.answer_id == answer_id,
            AnswerCitation.tenant_id == tenant_id,
            AnswerCitation.citation_number == citation_number,
        )
    )
    if citation is None:
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    document = session.get(Document, citation.document_id)
    version = session.get(DocumentVersion, citation.document_version_id)
    chunk = session.get(DocumentChunk, citation.document_chunk_id)
    if (
        document is None
        or version is None
        or chunk is None
        or document.tenant_id != tenant_id
        or version.tenant_id != tenant_id
        or chunk.tenant_id != tenant_id
        or version.document_id != document.id
        or chunk.document_version_id != version.id
        or version.status != "published"
    ):
        raise HTTPException(status_code=404, detail="Resource not found or access denied")
    authorize_space_access(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        space_id=document.space_id,
        manage=False,
    )
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.document_version_id == version.id,
            )
            .order_by(DocumentChunk.position)
        )
    )
    result = CitationSourceResponse(
        document=document,
        version=version,
        chunks=chunks,
        answer_id=answer_id,
        citation_number=citation_number,
        cited_chunk_id=chunk.id,
    )
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="knowledge.citation_viewed",
        resource_type="knowledge_answer",
        resource_id=answer_id,
        details={
            "citation_number": citation_number,
            "document_id": str(document.id),
            "version_number": version.version_number,
            "chunk_id": str(chunk.id),
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/tenants/{tenant_id}/questions",
    response_model=KnowledgeAnswerResponse,
    status_code=status.HTTP_201_CREATED,
)
def ask_knowledge_question(
    tenant_id: UUID,
    payload: KnowledgeQuestionCreate,
    user_id: CurrentUserId,
    session: DatabaseSession,
    request: Request,
    answer_provider: AnswerProvider,
    embedding_provider: Embedding,
) -> KnowledgeAnswerResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    allowed_space_ids = set(accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id))
    requested_space_ids = list(dict.fromkeys(payload.space_ids or []))
    scoped_space_ids = (
        [space_id for space_id in requested_space_ids if space_id in allowed_space_ids]
        if requested_space_ids
        else list(allowed_space_ids)
    )

    classifications = set(
        session.scalars(select(Space.classification).where(Space.id.in_(scoped_space_ids)))
    )
    external_ai_allowed = (
        "restricted" not in classifications or request.app.state.external_ai_allowed_for_restricted
    )
    query_embedding: list[float] | None = None
    retrieval_mode = "lexical"
    if (
        scoped_space_ids
        and embedding_provider.enabled
        and (not embedding_provider.external or external_ai_allowed)
    ):
        try:
            query_embedding = embedding_provider.embed([payload.question.strip()])[0]
            retrieval_mode = "hybrid"
        except (AIProviderError, IndexError):
            logger.warning("Semantic query embedding failed", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="语义检索服务暂时不可用，请稍后重试；这不代表资料中没有答案。",
            ) from None

    evidence = (
        []
        if not scoped_space_ids
        else search_published_knowledge(
            session,
            tenant_id=tenant_id,
            question=payload.question,
            user_id=user_id,
            space_ids=scoped_space_ids,
            query_embedding=query_embedding,
            embedding_model=(
                embedding_provider.model_name if query_embedding is not None else None
            ),
        )
    )
    selected_provider = answer_provider
    if answer_provider.external and not external_ai_allowed:
        selected_provider = request.app.state.local_answer_provider
    try:
        draft = selected_provider.generate(question=payload.question.strip(), evidence=evidence)
    except AIProviderError as error:
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=user_id,
            action="knowledge.answer_generation_failed",
            resource_type="knowledge_answer",
            outcome="failure",
            details={
                "provider": selected_provider.name,
                "model": selected_provider.model_name,
                "retrieval_mode": retrieval_mode,
            },
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured AI model is temporarily unavailable",
        ) from error
    # Generation may take a long time. A source can be withdrawn while the model
    # is running; do not send a newly generated answer based on stale permission.
    session.expire_all()
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    current_spaces = accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id)
    source_versions = {item.version.id for item in evidence}
    valid_versions = set(
        session.scalars(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.id.in_(source_versions),
                DocumentVersion.status == "published",
                DocumentVersion.tenant_id == tenant_id,
                Document.tenant_id == tenant_id,
                Document.space_id.in_(current_spaces),
                document_access_predicate(session, tenant_id, user_id),
            )
        )
    )
    if source_versions != valid_versions:
        raise HTTPException(status_code=409, detail="知识来源或访问权限已变化，请重新提问。")
    answer = KnowledgeAnswer(
        tenant_id=tenant_id,
        created_by=user_id,
        question=payload.question.strip(),
        answer=draft.text,
        status=draft.status,
        generator=draft.generator,
        model_name=draft.model_name,
    )
    session.add(answer)
    session.flush()
    for number, item in enumerate(draft.evidence, start=1):
        session.add(
            AnswerCitation(
                tenant_id=tenant_id,
                answer_id=answer.id,
                document_id=item.document.id,
                document_version_id=item.version.id,
                document_chunk_id=item.chunk.id,
                citation_number=number,
                document_title=item.document.title,
                filename=item.version.filename,
                version_number=item.version.version_number,
                locator=item.chunk.locator,
                excerpt=item.excerpt,
                relevance_score=item.score,
            )
        )
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="knowledge.question_answered",
        resource_type="knowledge_answer",
        resource_id=answer.id,
        details={
            "status": draft.status,
            "citation_count": len(draft.evidence),
            "space_count": len(scoped_space_ids),
            "retrieval_mode": retrieval_mode,
            "generator": draft.generator,
            "model": draft.model_name,
            "external_ai_restricted": not external_ai_allowed,
        },
    )
    session.commit()
    set_tenant_context(session, tenant_id)
    session.refresh(answer)
    return _response(session, answer)


@router.get(
    "/tenants/{tenant_id}/questions",
    response_model=list[KnowledgeAnswerResponse],
)
def list_question_history(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[KnowledgeAnswerResponse]:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    answers = list(
        session.scalars(
            select(KnowledgeAnswer)
            .where(
                KnowledgeAnswer.tenant_id == tenant_id,
                KnowledgeAnswer.created_by == user_id,
            )
            .order_by(KnowledgeAnswer.created_at.desc(), KnowledgeAnswer.id.desc())
            .limit(limit)
        )
    )
    allowed_space_ids = set(accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id))
    visible_answers: list[KnowledgeAnswerResponse] = []
    for answer in answers:
        # Recheck every source, including publication state. Hiding just a citation
        # would still leak its excerpt through the saved answer text.
        if _sources_visible(session, answer.id, allowed_space_ids, tenant_id, user_id):
            visible_answers.append(_response(session, answer))
    return visible_answers
