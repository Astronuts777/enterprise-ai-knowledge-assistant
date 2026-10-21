"""Tenant-scoped hybrid retrieval, grounded generation, and history endpoints."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.ai_providers import (
    AIProviderError,
    EmbeddingProvider,
    EvidenceAnswerProvider,
)
from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import AnswerCitation, Document, KnowledgeAnswer, Space
from enterprise_ai_api.retrieval import search_published_knowledge
from enterprise_ai_api.schemas import (
    AnswerCitationResponse,
    KnowledgeAnswerResponse,
    KnowledgeQuestionCreate,
)
from enterprise_ai_api.tenancy import (
    CurrentUserId,
    accessible_space_ids,
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
    allowed_space_ids = set(
        accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id)
    )
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
        "restricted" not in classifications
        or request.app.state.external_ai_allowed_for_restricted
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
            query_embedding = None

    evidence = (
        []
        if not scoped_space_ids
        else search_published_knowledge(
            session,
            tenant_id=tenant_id,
            question=payload.question,
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
    allowed_space_ids = set(
        accessible_space_ids(session, tenant_id=tenant_id, user_id=user_id)
    )
    visible_answers: list[KnowledgeAnswerResponse] = []
    for answer in answers:
        cited_space_ids = set(
            session.scalars(
                select(Document.space_id)
                .join(AnswerCitation, AnswerCitation.document_id == Document.id)
                .where(AnswerCitation.answer_id == answer.id)
            )
        )
        if cited_space_ids.issubset(allowed_space_ids):
            visible_answers.append(_response(session, answer))
    return visible_answers
