import json
from typing import Any
from uuid import uuid4

import pytest

from enterprise_ai_api.ai_providers import (
    AIProviderError,
    OpenAIAnswerProvider,
    OpenAIEmbeddingProvider,
)
from enterprise_ai_api.models import Document, DocumentChunk, DocumentVersion
from enterprise_ai_api.retrieval import RetrievedEvidence


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.path: str | None = None
        self.payload: dict[str, object] | None = None

    def post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        self.path = path
        self.payload = payload
        return self.response


def evidence(number: int) -> RetrievedEvidence:
    tenant_id = uuid4()
    document_id = uuid4()
    version_id = uuid4()
    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        space_id=uuid4(),
        title=f"Policy {number}",
        created_by=uuid4(),
    )
    version = DocumentVersion(
        id=version_id,
        tenant_id=tenant_id,
        document_id=document_id,
        version_number=1,
        filename=f"policy-{number}.md",
        media_type="text/markdown",
        byte_size=20,
        content_hash="a" * 64,
        object_key=f"objects/{number}",
        status="published",
        created_by=uuid4(),
    )
    chunk = DocumentChunk(
        id=uuid4(),
        tenant_id=tenant_id,
        document_version_id=version_id,
        position=1,
        locator="paragraph:1",
        text=f"Evidence text {number}",
    )
    return RetrievedEvidence(
        chunk=chunk,
        version=version,
        document=document,
        score=0.9,
        excerpt=chunk.text,
        lexical_score=2.0,
        semantic_score=0.9,
        classification="confidential",
    )


def test_embedding_provider_orders_vectors_and_requests_float_encoding() -> None:
    transport = FakeTransport(
        {
            "data": [
                {"index": 1, "embedding": [0, 1]},
                {"index": 0, "embedding": [1, 0]},
            ]
        }
    )
    provider = OpenAIEmbeddingProvider(
        transport=transport,
        model="text-embedding-test",
        dimensions=2,
    )

    result = provider.embed(["first", "second"])

    assert result == [[1.0, 0.0], [0.0, 1.0]]
    assert transport.path == "embeddings"
    assert transport.payload == {
        "model": "text-embedding-test",
        "input": ["first", "second"],
        "encoding_format": "float",
        "dimensions": 2,
    }


def test_answer_provider_validates_and_renumbers_model_citations() -> None:
    transport = FakeTransport(
        {
            "status": "completed",
            "output_text": json.dumps(
                {
                    "sufficient_evidence": True,
                    "claims": [
                        {
                            "text": "The policy permits two days [99]",
                            "citation_numbers": [2],
                        }
                    ],
                }
            ),
        }
    )
    provider = OpenAIAnswerProvider(transport=transport, model="answer-test")

    result = provider.generate(
        question="How many days?",
        evidence=[evidence(1), evidence(2)],
    )

    assert result.status == "grounded"
    assert result.text.endswith("The policy permits two days [1]")
    assert "[99]" not in result.text
    assert [item.document.title for item in result.evidence] == ["Policy 2"]
    assert transport.path == "responses"
    assert transport.payload is not None
    assert transport.payload["store"] is False
    text_config = transport.payload["text"]
    assert isinstance(text_config, dict)
    assert text_config["format"]["type"] == "json_schema"
    assert text_config["format"]["strict"] is True


def test_answer_provider_rejects_out_of_range_citation() -> None:
    transport = FakeTransport(
        {
            "output_text": json.dumps(
                {
                    "sufficient_evidence": True,
                    "claims": [{"text": "Unsupported", "citation_numbers": [2]}],
                }
            )
        }
    )
    provider = OpenAIAnswerProvider(transport=transport, model="answer-test")

    with pytest.raises(AIProviderError, match="outside the supplied range"):
        provider.generate(question="Question", evidence=[evidence(1)])
