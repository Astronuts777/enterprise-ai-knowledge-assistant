"""Permission-scoped hybrid lexical and encrypted-vector retrieval."""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion, Space

RETRIEVAL_BATCH_SIZE = 256
MAX_ANCHORS = 4
MAX_CITATIONS = 8
MAX_EXCERPT_CHARS = 1200

_LATIN_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*")
_CHINESE_SEQUENCE = re.compile(r"[\u3400-\u9fff]+")
_QUERY_FILLER = re.compile(
    r"可以|能否|能不能|为我|帮我|请问|请|提供|介绍|一下|相关内容|相关信息|吗|呢"
)
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


def _rank_key(item: RetrievedEvidence) -> tuple[float, str, int, str]:
    # Stable tie-breaking must not depend on DB row order or fetch batch size.
    return (-item.score, item.document.title, item.chunk.position, str(item.chunk.id))


def _retain_best(items: list[RetrievedEvidence], item: RetrievedEvidence) -> None:
    items.append(item)
    items.sort(key=_rank_key)
    del items[MAX_ANCHORS:]


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

    query_tokens = _tokens(_QUERY_FILLER.sub(" ", question))
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
            DocumentVersion.tenant_id == tenant_id,
            Document.tenant_id == tenant_id,
            Space.tenant_id == tenant_id,
        )
    )
    if space_ids is not None:
        if not space_ids:
            return []
        statement = statement.where(Document.space_id.in_(space_ids))

    normalized_question = _normalize(question)
    best_chunks: list[RetrievedEvidence] = []
    best_documents: list[RetrievedEvidence] = []
    # No corpus-size cutoff: SQL applies authorization/publication filters before
    # encrypted fields are loaded. Streaming bounds ORM/driver buffering, while
    # two top-k lists bound retained matches even when every chunk is relevant.
    with session.execute(statement.execution_options(yield_per=RETRIEVAL_BATCH_SIZE)) as rows:
        for chunk, version, document, space in rows:
            content_lexical_score = _score(normalized_question, query_tokens, chunk.text)
            metadata_lexical_score = (
                _score(normalized_question, query_tokens, f"{document.title} {version.filename}")
                if chunk.position == 1 else 0
            )
            lexical_score = max(content_lexical_score, metadata_lexical_score)
            semantic_score = _semantic_score(
                query_embedding, chunk.embedding,
                query_model=embedding_model, chunk_model=chunk.embedding_model,
            )
            score = _hybrid_score(lexical_score, semantic_score)
            if score is None:
                continue
            item = RetrievedEvidence(
                chunk=chunk,
                version=version,
                document=document,
                score=score,
                excerpt=_best_excerpt(chunk.text, query_tokens),
                lexical_score=lexical_score,
                semantic_score=semantic_score,
                classification=space.classification,
            )
            _retain_best(best_chunks, item)
            existing = next((entry for entry in best_documents
                             if entry.document.id == document.id), None)
            if existing is None:
                _retain_best(best_documents, item)
            elif _rank_key(item) < _rank_key(existing):
                best_documents.remove(existing)
                _retain_best(best_documents, item)

    # Reserve one anchor per document, then fill unused slots with global top-k.
    anchors = list(best_documents)
    anchor_ids = {item.chunk.id for item in anchors}
    for item in best_chunks:
        if len(anchors) == MAX_ANCHORS:
            break
        if item.chunk.id not in anchor_ids:
            anchors.append(item)
            anchor_ids.add(item.chunk.id)
    if not anchors:
        return []
    selected = list(anchors)
    selected_ids = {item.chunk.id for item in selected}
    # Fetch only the at-most-eight adjacent positions, with exactly the same
    # tenant/space/publication predicates. Close the scan cursor before this query.
    neighbor_positions = [and_(
        DocumentChunk.document_version_id == anchor.version.id,
        DocumentChunk.position.in_([anchor.chunk.position - 1, anchor.chunk.position + 1]),
    ) for anchor in anchors]
    neighbors = {(chunk.document_version_id, chunk.position): (chunk, version, document, space)
                 for chunk, version, document, space in session.execute(
                     statement.where(or_(*neighbor_positions))
                 )}
    for offset in (1, -1):
        for anchor in anchors:
            neighbor = neighbors.get((anchor.version.id, anchor.chunk.position + offset))
            if neighbor is None or neighbor[0].id in selected_ids:
                continue
            chunk, version, document, space = neighbor
            selected.append(RetrievedEvidence(
                chunk=chunk, version=version, document=document,
                score=anchor.score * 0.8, excerpt=_best_excerpt(chunk.text, query_tokens),
                lexical_score=0, semantic_score=None, classification=space.classification,
            ))
            selected_ids.add(chunk.id)
            if len(selected) == MAX_CITATIONS:
                return selected
    return selected


def build_grounded_answer(evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    if not evidence:
        return (
            "本次检索未找到匹配的已发布正文，这不代表文件中一定没有答案。"
            "请检查所选空间、文档解析内容及语义索引状态，或使用原文关键词重试。",
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
        len(token) >= 4
        and any(character.isalpha() for character in token)
        and (any(character.isdigit() for character in token) or "-" in token or "_" in token)
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
    value = text.strip()
    if len(value) <= MAX_EXCERPT_CHARS:
        return value
    # Select a window around the strongest match, not the start of a long sentence.
    starts = range(0, len(value), MAX_EXCERPT_CHARS // 2)
    start = max(starts, key=lambda index: len(
        query_tokens & _tokens(value[index:index + MAX_EXCERPT_CHARS])
    ))
    return ("…" if start else "") + value[start:start + MAX_EXCERPT_CHARS] + (
        "…" if start + MAX_EXCERPT_CHARS < len(value) else ""
    )
