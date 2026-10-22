"""Permission-scoped hybrid lexical and encrypted-vector retrieval."""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion, Space

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
    lexical_score: float
    semantic_score: float | None
    classification: str


def search_published_knowledge(
    session: Session,
    *,
    tenant_id: UUID,
    question: str,
    space_ids: list[UUID] | None = None,
    query_embedding: list[float] | None = None,
    embedding_model: str | None = None,
) -> list[RetrievedEvidence]:
    """Rank published chunks using lexical coverage and optional cosine similarity."""

    query_tokens = _tokens(question)
    if not query_tokens and query_embedding is None:
        return []

    statement = (
        select(DocumentChunk, DocumentVersion, Document, Space)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Space, Space.id == Document.space_id)
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
    for chunk, version, document, space in session.execute(statement):
        content_lexical_score = _score(normalized_question, query_tokens, chunk.text)
        metadata_lexical_score = (
            _score(
                normalized_question,
                query_tokens,
                f"{document.title} {version.filename}",
            )
            if chunk.position == 1
            else 0
        )
        lexical_score = max(content_lexical_score, metadata_lexical_score)
        semantic_score = _semantic_score(
            query_embedding,
            chunk.embedding,
            query_model=embedding_model,
            chunk_model=chunk.embedding_model,
        )
        score = _hybrid_score(lexical_score, semantic_score)
        if score is None:
            continue
        ranked.append(
            RetrievedEvidence(
                chunk=chunk,
                version=version,
                document=document,
                score=score,
                excerpt=_best_excerpt(chunk.text, query_tokens),
                lexical_score=lexical_score,
                semantic_score=semantic_score,
                classification=space.classification,
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
    if not query_tokens:
        return 0
    normalized_text = _normalize(text)
    text_tokens = _tokens(normalized_text)
    shared = query_tokens & text_tokens
    if not shared:
        return 0

    coverage = len(shared) / len(query_tokens)
    high_signal_matches = {token for token in shared if _is_high_signal_token(token)}
    if len(query_tokens) >= 4 and coverage < 0.2 and not high_signal_matches:
        return 0

    frequency = sum(min(normalized_text.count(token), 3) for token in shared)
    identifier_bonus = len(high_signal_matches) * 4
    phrase_bonus = (
        8 if len(normalized_question) >= 4 and normalized_question in normalized_text else 0
    )
    return round((coverage * 10) + frequency + identifier_bonus + phrase_bonus, 4)


def _is_high_signal_token(token: str) -> bool:
    """Recognize order numbers, codes, and other distinctive Latin identifiers."""

    return bool(_LATIN_TOKEN.fullmatch(token)) and (
        len(token) >= 6
        or any(character.isdigit() for character in token)
        or "-" in token
        or "_" in token
    )


def _semantic_score(
    query_embedding: list[float] | None,
    stored_embedding: str | None,
    *,
    query_model: str | None,
    chunk_model: str | None,
) -> float | None:
    if (
        query_embedding is None
        or stored_embedding is None
        or query_model is None
        or query_model != chunk_model
    ):
        return None
    try:
        raw_vector = json.loads(stored_embedding)
        chunk_embedding = [float(value) for value in raw_vector]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if len(query_embedding) != len(chunk_embedding) or not chunk_embedding:
        return None
    dot_product = sum(
        left * right
        for left, right in zip(query_embedding, chunk_embedding, strict=True)
    )
    query_norm = math.sqrt(sum(value * value for value in query_embedding))
    chunk_norm = math.sqrt(sum(value * value for value in chunk_embedding))
    if query_norm == 0 or chunk_norm == 0:
        return None
    similarity = dot_product / (query_norm * chunk_norm)
    if not math.isfinite(similarity):
        return None
    return max(-1.0, min(1.0, similarity))


def _hybrid_score(lexical_score: float, semantic_score: float | None) -> float | None:
    lexical_normalized = min(lexical_score / 20, 1.0)
    if semantic_score is None:
        return round(lexical_normalized, 6) if lexical_score > 0 else None
    semantic_normalized = max(0.0, semantic_score)
    if lexical_score <= 0 and semantic_normalized < 0.35:
        return None
    if lexical_score <= 0:
        return round(semantic_normalized * 0.85, 6)
    return round((lexical_normalized * 0.55) + (semantic_normalized * 0.45), 6)


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
