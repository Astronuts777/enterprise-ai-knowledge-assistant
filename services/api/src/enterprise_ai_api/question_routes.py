"""Tenant-scoped evidence retrieval, answer persistence, and history endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.database import get_session, set_tenant_context
from enterprise_ai_api.models import AnswerCitation, KnowledgeAnswer, Space
from enterprise_ai_api.retrieval import build_grounded_answer, search_published_knowledge
from enterprise_ai_api.schemas import (
    AnswerCitationResponse,
    KnowledgeAnswerResponse,
    KnowledgeQuestionCreate,
)
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1")
DatabaseSession = Annotated[Session, Depends(get_session)]


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
) -> KnowledgeAnswerResponse:
    authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    scoped_space_ids = list(dict.fromkeys(payload.space_ids or []))
    if scoped_space_ids:
        existing_space_ids = set(
            session.scalars(
                select(Space.id).where(
                    Space.tenant_id == tenant_id,
                    Space.id.in_(scoped_space_ids),
                )
            )
        )
        scoped_space_ids = [
            space_id for space_id in scoped_space_ids if space_id in existing_space_ids
        ]

    evidence = (
        []
        if payload.space_ids and not scoped_space_ids
        else search_published_knowledge(
            session,
            tenant_id=tenant_id,
            question=payload.question,
            space_ids=scoped_space_ids or None,
        )
    )
    answer_text, answer_status = build_grounded_answer(evidence)
    answer = KnowledgeAnswer(
        tenant_id=tenant_id,
        created_by=user_id,
        question=payload.question.strip(),
        answer=answer_text,
        status=answer_status,
    )
    session.add(answer)
    session.flush()
    for number, item in enumerate(evidence, start=1):
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
    return [_response(session, answer) for answer in answers]
