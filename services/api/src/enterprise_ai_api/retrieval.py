"""Deterministic local retrieval for the first evidence-grounded answer slice."""

import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion

MAX_CANDIDATE_CHUNKS = 5000
MAX_CITATIONS = 4
MAX_EXCERPT_CHARS = 320

_LATIN_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CHINESE_SEQUENCE = re.compile(r"[\u3400-\u9fff]+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？.!?])\s+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "in",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "why",
    "了",
    "什么",
    "以及",
    "可以",
    "哪些",
    "如何",
    "是否",
    "的",
    "请问",
    "这个",
}


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    chunk: DocumentChunk
    version: DocumentVersion
    document: Document
    score: float
    excerpt: str


def search_published_knowledge(
    session: Session,
    *,
    tenant_id: UUID,
    question: str,
    space_ids: list[UUID] | None = None,
) -> list[RetrievedEvidence]:
    """Rank published chunks using transparent local lexical matching."""

    query_tokens = _tokens(question)
    if not query_tokens:
        return []

    statement = (
        select(DocumentChunk, DocumentVersion, Document)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentChunk.tenant_id == tenant_id,
            DocumentVersion.status == "published",
            Document.tenant_id == tenant_id,
        )
        .order_by(DocumentChunk.created_at.desc(), DocumentChunk.position)
        .limit(MAX_CANDIDATE_CHUNKS)
    )
    if space_ids:
        statement = statement.where(Document.space_id.in_(space_ids))

    normalized_question = _normalize(question)
    ranked: list[RetrievedEvidence] = []
    for chunk, version, document in session.execute(statement):
        score = _score(normalized_question, query_tokens, chunk.text)
        if score <= 0:
            continue
        ranked.append(
            RetrievedEvidence(
                chunk=chunk,
                version=version,
                document=document,
                score=score,
                excerpt=_best_excerpt(chunk.text, query_tokens),
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.document.title, item.chunk.position))
    return ranked[:MAX_CITATIONS]


def build_grounded_answer(evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    if not evidence:
        return (
            "未在已发布的企业资料中找到足够依据。请补充或发布相关资料后再试。",
            "insufficient_evidence",
        )

    statements = [f"{item.excerpt} [{index}]" for index, item in enumerate(evidence, start=1)]
    return "根据已发布的企业资料：\n\n" + "\n\n".join(statements), "grounded"


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _tokens(value: str) -> set[str]:
    normalized = _normalize(value)
    tokens = {token for token in _LATIN_TOKEN.findall(normalized) if token not in _STOPWORDS}
    for sequence in _CHINESE_SEQUENCE.findall(normalized):
        if len(sequence) == 1 and sequence not in _STOPWORDS:
            tokens.add(sequence)
        else:
            tokens.update(
                sequence[index : index + 2]
                for index in range(len(sequence) - 1)
                if sequence[index : index + 2] not in _STOPWORDS
            )
    return tokens


def _score(normalized_question: str, query_tokens: set[str], text: str) -> float:
    normalized_text = _normalize(text)
    text_tokens = _tokens(normalized_text)
    shared = query_tokens & text_tokens
    if not shared:
        return 0

    coverage = len(shared) / len(query_tokens)
    if len(query_tokens) >= 4 and coverage < 0.2:
        return 0

    frequency = sum(min(normalized_text.count(token), 3) for token in shared)
    phrase_bonus = (
        8 if len(normalized_question) >= 4 and normalized_question in normalized_text else 0
    )
    return round((coverage * 10) + frequency + phrase_bonus, 4)


def _best_excerpt(text: str, query_tokens: set[str]) -> str:
    normalized_sentences = [
        value.strip() for value in _SENTENCE_BOUNDARY.split(text) if value.strip()
    ]
    if not normalized_sentences:
        normalized_sentences = [text.strip()]
    best = max(
        normalized_sentences,
        key=lambda value: len(query_tokens & _tokens(value)),
    )
    if len(best) <= MAX_EXCERPT_CHARS:
        return best
    return best[: MAX_EXCERPT_CHARS - 1].rstrip() + "…"
