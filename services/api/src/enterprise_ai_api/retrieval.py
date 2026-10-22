"""Permission-scoped hybrid lexical and encrypted-vector retrieval."""

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, literal, or_, select
from sqlalchemy.orm import Session, defer

from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion, Space

RETRIEVAL_BATCH_SIZE = 256
MAX_ANCHORS = 4
MAX_CITATIONS = 8
MAX_EXCERPT_CHARS = 1200
MAX_LOCAL_PASSAGES = 3
LOCAL_EXCERPT_BUDGET = 1200

_HEADING_LINE = re.compile(r"^(?:#{1,6}\s+.+|[-=*_]{3,})$")
_QUALIFIER_START = re.compile(
    r"^(?:但|但是|不过|然而|除非|除外|例外|除.*外|仅限|仅当|否则|注意[：:]|"
    r"未经|不得|不适用|前提|条件[：:]|however\b|except\b|unless\b|only\b|"
    r"provided\b|subject to\b|not applicable\b|do not\b)", re.IGNORECASE,
)

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


@dataclass(frozen=True, slots=True)
class _Candidate:
    chunk_id: UUID
    version_id: UUID
    document_id: UUID
    title: str
    position: int
    score: float
    lexical_score: float
    semantic_score: float | None


def _rank_key(item: _Candidate) -> tuple[float, str, int, str]:
    # Stable tie-breaking must not depend on DB row order or fetch batch size.
    return (-item.score, item.title, item.position, str(item.chunk_id))


def _retain_best(items: list[_Candidate], item: _Candidate) -> None:
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

    # Only matching-model vectors are needed for scoring. The CASE expression
    # retains EncryptedText's result processor; never cast ciphertext to plaintext.
    vector_column = (
        case((DocumentChunk.embedding_model == embedding_model, DocumentChunk.embedding))
        if query_embedding is not None and embedding_model is not None else literal(None)
    )
    statement = (
        select(
            DocumentChunk.id, DocumentChunk.document_version_id, Document.id,
            Document.title, DocumentChunk.position, DocumentVersion.filename,
            DocumentChunk.text, vector_column, DocumentChunk.embedding_model,
        )
        .select_from(DocumentChunk)
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
    query_norm = (
        math.sqrt(sum(value * value for value in query_embedding))
        if query_embedding is not None else None
    )
    best_chunks: list[_Candidate] = []
    best_documents: list[_Candidate] = []
    # No corpus-size cutoff: SQL applies authorization/publication filters before
    # encrypted fields are loaded. Scalar rows avoid ORM objects for the corpus;
    # only IDs/scores are retained and excerpts are delayed until after ranking.
    with session.execute(statement.execution_options(yield_per=RETRIEVAL_BATCH_SIZE)) as rows:
        for (
            chunk_id, version_id, document_id, title, position, filename, body, vector, model,
        ) in rows:
            content_lexical_score = _score(normalized_question, query_tokens, body)
            metadata_lexical_score = (
                _score(normalized_question, query_tokens, f"{title} {filename}")
                if position == 1 else 0
            )
            lexical_score = max(content_lexical_score, metadata_lexical_score)
            semantic_score = _semantic_score(
                query_embedding, vector,
                query_model=embedding_model, chunk_model=model, query_norm=query_norm,
            )
            score = _hybrid_score(lexical_score, semantic_score)
            if score is None:
                continue
            item = _Candidate(
                chunk_id=chunk_id, version_id=version_id, document_id=document_id,
                title=title, position=position,
                score=score,
                lexical_score=lexical_score,
                semantic_score=semantic_score,
            )
            _retain_best(best_chunks, item)
            existing = next((entry for entry in best_documents
                             if entry.document_id == document_id), None)
            if existing is None:
                _retain_best(best_documents, item)
            elif _rank_key(item) < _rank_key(existing):
                best_documents.remove(existing)
                _retain_best(best_documents, item)

    # Reserve one anchor per document, then fill unused slots with global top-k.
    anchors = list(best_documents)
    anchor_ids = {item.chunk_id for item in anchors}
    for item in best_chunks:
        if len(anchors) == MAX_ANCHORS:
            break
        if item.chunk_id not in anchor_ids:
            anchors.append(item)
            anchor_ids.add(item.chunk_id)
    if not anchors:
        return []
    # Hydrate at most four anchors plus eight adjacent positions, after closing
    # the scan cursor. Reuse every SQL scope predicate, including publication.
    # Vectors are not evidence and must not be fetched/decrypted in this phase.
    neighbor_positions = [and_(
        DocumentChunk.document_version_id == anchor.version_id,
        DocumentChunk.position.in_([anchor.position - 1, anchor.position + 1]),
    ) for anchor in anchors]
    evidence_statement = statement.with_only_columns(
        DocumentChunk, DocumentVersion, Document, Space,
    ).where(or_(DocumentChunk.id.in_(anchor_ids), *neighbor_positions)).options(
        defer(DocumentChunk.embedding, raiseload=True),
    ).execution_options(populate_existing=True)
    with session.execute(evidence_statement) as rows:
        hydrated = {chunk.id: (chunk, version, document, space)
                    for chunk, version, document, space in rows}
    neighbors = {(chunk.document_version_id, chunk.position): (chunk, version, document, space)
                 for chunk, version, document, space in hydrated.values()}
    selected = []
    # A version may have been withdrawn during the scan; do not return it or
    # expand its context if it is absent from the freshly scoped evidence query.
    anchors = [anchor for anchor in anchors if anchor.chunk_id in hydrated]
    for anchor in anchors:
        chunk, version, document, space = hydrated[anchor.chunk_id]
        selected.append(RetrievedEvidence(
            chunk=chunk, version=version, document=document, score=anchor.score,
            excerpt=_best_excerpt(chunk.text, query_tokens),
            lexical_score=anchor.lexical_score, semantic_score=anchor.semantic_score,
            classification=space.classification,
        ))
    selected_ids = {item.chunk.id for item in selected}
    for offset in (1, -1):
        for anchor in anchors:
            neighbor = neighbors.get((anchor.version_id, anchor.position + offset))
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


def select_local_answer_evidence(
    question: str, evidence: list[RetrievedEvidence],
) -> list[RetrievedEvidence]:
    """Distinguish retrieval context from passages worth displaying as an answer.

    Selection is request-local and only consumes already-authorized evidence.
    Do not summarize, splice sentences, or remove conditions from a passage.
    Model providers still receive the complete retrieval context.
    """
    tokens = _tokens(_QUERY_FILLER.sub(" ", question))
    normalized = _normalize(question)
    passages = []
    for item in evidence:
        lines = [line.strip() for line in item.excerpt.splitlines() if line.strip()]
        if not lines or all(_HEADING_LINE.fullmatch(line) for line in lines) or (
            len(lines) == 2 and re.fullmatch(r"[-=]{3,}", lines[1])
        ):
            continue
        lexical = _score(normalized, tokens, item.excerpt)
        shared = tokens & _tokens(item.excerpt)
        passages.append((item, lexical, shared))

    best_lexical = max((score for _, score, _ in passages), default=0)
    best_semantic = max((item.semantic_score or 0 for item, _, _ in passages), default=0)
    # Metadata-only document lookups can still show a body excerpt. A title
    # alone is never an answer. Do not use this fallback if a body match exists.
    metadata_versions = {item.version.id for item in evidence if item.lexical_score > 0}
    passages.sort(key=lambda row: (
        -row[1], -(row[0].semantic_score or 0), -row[0].score,
        row[0].document.title, row[0].chunk.position, str(row[0].chunk.id),
    ))
    selected: list[RetrievedEvidence] = []
    covered: set[str] = set()
    seen: set[str] = set()
    used_chars = 0
    for item, lexical, shared in passages:
        semantic_match = (
            item.semantic_score is not None
            and item.semantic_score >= max(0.35, best_semantic * 0.8)
        )
        lexical_match = lexical > 0 and (
            lexical >= best_lexical * 0.65
            or (bool(shared - covered) and lexical >= best_lexical * 0.25)
        )
        metadata_match = (
            best_lexical == 0 and best_semantic < 0.35
            and item.version.id in metadata_versions
        )
        if not (lexical_match or semantic_match or metadata_match):
            continue
        key = " ".join(item.excerpt.split())
        if key in seen:
            continue
        if len(selected) >= MAX_LOCAL_PASSAGES:
            break
        if selected and used_chars + len(item.excerpt) > LOCAL_EXCERPT_BUDGET:
            continue
        selected.append(item)
        covered.update(shared)
        seen.add(key)
        used_chars += len(item.excerpt)

    # Explicit adjacent exceptions/conditions must not disappear merely to meet
    # a brevity budget. Preserve them verbatim, in source order, with their own
    # citations; the bounded retrieval set remains the absolute limit.
    anchors = list(selected)
    for item, _, _ in passages:
        key = " ".join(item.excerpt.split())
        if key in seen:
            continue
        anchor = next((entry for entry in anchors if entry.version.id == item.version.id
                       and abs(entry.chunk.position - item.chunk.position) == 1
                       and (
                           _QUALIFIER_START.match(item.excerpt.lstrip())
                           or (item.chunk.position == entry.chunk.position + 1
                               and len(entry.excerpt) <= 80
                               and _tokens(entry.excerpt) <= tokens
                               and not re.search(r"\d|[。!?！？;；]", entry.excerpt))
                       )), None)
        if anchor is None:
            continue
        index = selected.index(anchor) + (item.chunk.position > anchor.chunk.position)
        selected.insert(index, item)
        seen.add(key)
    return selected


def build_grounded_answer(evidence: list[RetrievedEvidence]) -> tuple[str, str]:
    if not evidence:
        return (
            "本次检索未找到匹配的已发布正文，这不代表文件中一定没有答案。"
            "请检查所选空间、文档解析内容及语义索引状态，或使用原文关键词重试。",
            "insufficient_evidence",
        )

    statements = [f"{item.excerpt} [{index}]" for index, item in enumerate(evidence, start=1)]
    return "相关原文节选（完整上下文见引用）：\n\n" + "\n\n".join(statements), "grounded"


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
    query_norm: float | None = None,
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
    if query_norm is None:
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
