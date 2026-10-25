"""Tenant lobby and participant-only direct messages; never a document access channel."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_ai_api.audit import record_audit_event
from enterprise_ai_api.database import get_session
from enterprise_ai_api.models import ChatConversation, ChatMessage, Membership, User
from enterprise_ai_api.tenancy import CurrentUserId, authorize_tenant_action
from enterprise_ai_core.security import Action

router = APIRouter(prefix="/v1/tenants/{tenant_id}/chat")
DatabaseSession = Annotated[Session, Depends(get_session)]


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    peer_user_id: UUID | None = None


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Message must contain text and no null characters")
        return value


class TimestampResponse(BaseModel):
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        # SQLite stores server-default UTC timestamps without a timezone suffix.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ConversationResponse(TimestampResponse):
    id: UUID
    kind: Literal["lobby", "direct"]
    peer_user_id: UUID | None
    can_send: bool


class MessageResponse(TimestampResponse):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sequence: int
    sender_id: UUID
    client_id: UUID
    body: str


class MessagePage(BaseModel):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    has_more: bool


def _active(session, tenant_id, user_id):
    # Select only metadata, never decrypt an unrelated member's profile or credentials.
    return (
        session.scalar(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
                Membership.active.is_(True),
                Membership.provisioning_active.is_(True),
                User.status == "active",
            )
        )
        is not None
    )


def _authorize(session, tenant_id, user_id, *, lock=False):
    tenant = authorize_tenant_action(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        action=Action.READ_KNOWLEDGE,
    )
    if tenant.status != "active" or not _active(session, tenant_id, user_id):
        raise HTTPException(404, "Resource not found or access denied")
    if lock:
        # Serialize writes per sender (including rate limits) against membership changes.
        session.execute(
            update(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == user_id,
            )
            .values(role=Membership.role)
        )
        session.expire_all()
        _authorize(session, tenant_id, user_id)


def _visible(tenant_id, user_id):
    return (
        ChatConversation.tenant_id == tenant_id,
        or_(
            ChatConversation.kind == "lobby",
            ChatConversation.participant_a == user_id,
            ChatConversation.participant_b == user_id,
        ),
    )


def _response(session, conversation, user_id):
    peer = None
    if conversation.kind == "direct":
        peer = (
            conversation.participant_b
            if conversation.participant_a == user_id
            else conversation.participant_a
        )
    return ConversationResponse(
        id=conversation.id,
        kind=conversation.kind,
        peer_user_id=peer,
        can_send=peer is None or _active(session, conversation.tenant_id, peer),
        created_at=conversation.created_at,
    )


@router.post("/conversations", response_model=ConversationResponse)
def open_conversation(
    tenant_id: UUID, payload: ConversationInput, user_id: CurrentUserId, session: DatabaseSession
):
    _authorize(session, tenant_id, user_id, lock=True)
    peer = payload.peer_user_id
    if peer is not None and (peer == user_id or not _active(session, tenant_id, peer)):
        raise HTTPException(404, "Choose another active member of this enterprise")
    pair = sorted([user_id, peer], key=str) if peer else [None, None]
    key = f"direct:{pair[0]}:{pair[1]}" if peer else "lobby"
    query = select(ChatConversation).where(
        ChatConversation.tenant_id == tenant_id,
        ChatConversation.conversation_key == key,
    )
    conversation = session.scalar(query)
    if conversation is None:
        try:
            with session.begin_nested():
                conversation = ChatConversation(
                    tenant_id=tenant_id,
                    conversation_key=key,
                    kind="direct" if peer else "lobby",
                    participant_a=pair[0],
                    participant_b=pair[1],
                )
                session.add(conversation)
                session.flush()
        except IntegrityError:
            # Simultaneous opens by both participants resolve to the same conversation.
            conversation = session.scalar(query)
            if conversation is None:
                raise
    result = _response(session, conversation, user_id)
    session.commit()
    return result


@router.get("/conversations", response_model=list[ConversationResponse])
def conversations(
    tenant_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    _authorize(session, tenant_id, user_id)
    rows = session.scalars(
        select(ChatConversation)
        .where(*_visible(tenant_id, user_id))
        .order_by(ChatConversation.created_at.desc(), ChatConversation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return [_response(session, item, user_id) for item in rows]


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def messages(
    tenant_id: UUID,
    conversation_id: UUID,
    user_id: CurrentUserId,
    session: DatabaseSession,
    before: Annotated[int | None, Query(ge=1)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    _authorize(session, tenant_id, user_id)
    if before is not None and after is not None:
        raise HTTPException(422, "Use either before or after, not both")
    conversation = session.scalar(
        select(ChatConversation).where(
            *_visible(tenant_id, user_id),
            ChatConversation.id == conversation_id,
        )
    )
    if conversation is None:
        raise HTTPException(404, "Resource not found or access denied")
    # Explicit tenant AND participant SQL filters precede ORM message decryption.
    query = (
        select(ChatMessage)
        .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
        .where(
            *_visible(tenant_id, user_id),
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.conversation_id == conversation_id,
        )
    )
    if before is not None:
        query = query.where(ChatMessage.sequence < before)
    if after is not None:
        query = query.where(ChatMessage.sequence > after).order_by(ChatMessage.sequence)
    else:
        query = query.order_by(ChatMessage.sequence.desc())
    rows = list(session.scalars(query.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    if after is None:
        rows.reverse()
    return MessagePage(
        conversation=_response(session, conversation, user_id),
        messages=[MessageResponse.model_validate(row) for row in rows],
        has_more=has_more,
    )


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
def send_message(
    tenant_id: UUID,
    conversation_id: UUID,
    payload: MessageInput,
    user_id: CurrentUserId,
    session: DatabaseSession,
):
    _authorize(session, tenant_id, user_id, lock=True)
    locked = session.execute(
        update(ChatConversation)
        .where(
            *_visible(tenant_id, user_id),
            ChatConversation.id == conversation_id,
        )
        .values(last_sequence=ChatConversation.last_sequence)
    )
    if not locked.rowcount:
        raise HTTPException(404, "Resource not found or access denied")
    session.expire_all()
    conversation = session.scalar(
        select(ChatConversation).where(
            *_visible(tenant_id, user_id),
            ChatConversation.id == conversation_id,
        )
    )
    if not _response(session, conversation, user_id).can_send:
        raise HTTPException(409, "对方当前不是有效成员，不能发送新消息。")
    existing = session.scalar(
        select(ChatMessage).where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.sender_id == user_id,
            ChatMessage.client_id == payload.client_id,
        )
    )
    if existing:
        if existing.body != payload.body:
            raise HTTPException(409, "重试标识已用于其他内容，请刷新后重试。")
        result = MessageResponse.model_validate(existing)
        session.commit()
        return result
    count = session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(
            ChatMessage.tenant_id == tenant_id,
            ChatMessage.sender_id == user_id,
            ChatMessage.created_at >= datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    if count >= 30:
        raise HTTPException(429, "发送过于频繁，请稍后再试。", headers={"Retry-After": "60"})
    conversation.last_sequence += 1
    message = ChatMessage(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        sequence=conversation.last_sequence,
        sender_id=user_id,
        client_id=payload.client_id,
        body=payload.body,
    )
    session.add(message)
    session.flush()
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=user_id,
        action="chat.message_sent",
        resource_type="chat_message",
        resource_id=message.id,
    )
    result = MessageResponse.model_validate(message)
    session.commit()
    return result
