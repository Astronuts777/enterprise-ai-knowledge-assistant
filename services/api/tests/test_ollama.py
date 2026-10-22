import json
from io import BytesIO
from unittest.mock import Mock

import pytest

from enterprise_ai_api import ai_providers as ai


@pytest.mark.parametrize("url", ["https://ollama.com", "http://192.168.1.2:11434",
                                 "http://localhost:11434/path", "http://u:p@127.0.0.1"])
def test_local_provider_rejects_non_loopback_or_ambiguous_endpoints(monkeypatch, url):
    monkeypatch.setenv("OLLAMA_BASE_URL", url)
    with pytest.raises(RuntimeError, match="loopback"):
        ai.OllamaTransport()


def test_native_embedding_protocol_and_cloud_block(monkeypatch):
    opener = Mock()
    opener.open.return_value = BytesIO(json.dumps({"embeddings": [[1, 0], [0, 1]]}).encode())
    builder = Mock(return_value=opener)
    monkeypatch.setattr(ai, "build_opener", builder)
    provider = ai.OllamaEmbeddingProvider(transport=ai.OllamaTransport(), model="bge-m3")
    assert provider.embed(["中文", "English"]) == [[1, 0], [0, 1]]
    req = opener.open.call_args.args[0]
    assert req.full_url == "http://127.0.0.1:11434/api/embed"
    assert json.loads(req.data)["truncate"] is False
    assert builder.call_args.args[0].proxies == {}
    assert provider.external is False
    with pytest.raises(ai.AIProviderError, match="Cloud models"):
        ai.OllamaTransport().post("embeddings", {"model": "qwen-cloud", "input": ["secret"]})


def test_native_chat_protocol_disables_thinking_and_requires_complete_output(monkeypatch):
    opener = Mock()
    result = {"done": True, "message": {"content": '{"sufficient_evidence":false,"claims":[]}'}}
    opener.open.return_value = BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(ai, "build_opener", lambda *args: opener)
    payload = {"model": "qwen3:4b", "input": [{"content": "question and evidence"}]}
    output = ai.OllamaTransport().post("responses", payload)
    request = opener.open.call_args.args[0]
    body = json.loads(request.data)
    assert request.full_url.endswith("/api/chat")
    assert body["think"] is False and body["stream"] is False
    assert body["truncate"] is False and body["shift"] is False
    assert body["format"] == ai.GROUNDED_ANSWER_SCHEMA
    assert output["output_text"] == result["message"]["content"]
    result["done_reason"] = "length"
    opener.open.return_value = BytesIO(json.dumps(result).encode())
    with pytest.raises(ai.AIProviderError):
        ai.OllamaTransport().post("responses", payload)


def test_environment_creates_private_local_providers(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    answer = ai.answer_provider_from_environment()
    embedding = ai.embedding_provider_from_environment()
    assert answer.name == "ollama" and answer.model_name == "qwen3:4b"
    assert embedding.model_name == "bge-m3" and embedding.enabled
    assert not answer.external and not embedding.external
