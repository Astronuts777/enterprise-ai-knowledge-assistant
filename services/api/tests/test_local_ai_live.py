"""Opt-in real inference: RUN_LOCAL_AI_TESTS=1 .venv/bin/pytest -s -k local_ai_live."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enterprise_ai_api.ai_providers import (
    OllamaAnswerProvider,
    OllamaEmbeddingProvider,
    OllamaTransport,
)
from enterprise_ai_api.main import create_app

pytestmark = pytest.mark.skipif(os.getenv("RUN_LOCAL_AI_TESTS") != "1",
                                reason="Requires local Ollama models; no cloud calls")


def test_local_ai_live_cross_language_multi_document_and_refusal(tmp_path: Path):
    transport = OllamaTransport()
    app = create_app(
        "sqlite://", storage_root=str(tmp_path / "uploads"),
        answer_provider=OllamaAnswerProvider(transport=transport, model="qwen3:4b"),
        embedding_provider=OllamaEmbeddingProvider(transport=transport, model="bge-m3"),
    )
    headers = {"X-Dev-User-Id": "00000000-0000-0000-0000-00000000000a"}
    with TestClient(app) as client:
        tenant = client.post("/v1/tenants", json={"name": "Local AI test"},
                             headers=headers).json()["id"]
        space = client.post(f"/v1/tenants/{tenant}/spaces",
                            json={"name": "Travel", "classification": "restricted"},
                            headers=headers).json()["id"]
        for title, body in [
            ("Accommodation policy", "Employees on business travel can claim hotel expenses.\n\n"
             "The reimbursement ceiling is AUD 180 per night, including taxes."),
            ("Travel approval", "Before departure, employees must obtain written approval "
             "from their direct manager. Receipts must be kept for all claims."),
            ("Payroll schedule", "Monthly salaries are paid on the 25th of each month."),
        ]:
            upload = client.post(f"/v1/tenants/{tenant}/spaces/{space}/documents",
                                 data={"title": title}, files={"file": (f"{title}.txt", body)},
                                 headers=headers)
            assert upload.status_code == 201, upload.text
            version = upload.json()["version"]
            assert version["status"] == "ready", version
            assert client.post(f"/v1/tenants/{tenant}/document-versions/{version['id']}/publish",
                               headers=headers).status_code == 200
        status = client.get(f"/v1/tenants/{tenant}/knowledge-status", headers=headers).json()
        assert status["published_chunks"] == status["indexed_chunks"] == 4, status
        for question, expected in [
            ("出差住酒店每晚最多报销多少澳元，出发前需要谁批准？", "multi"),
            ("What is the nightly lodging reimbursement ceiling?", "single"),
            ("明天悉尼会下雨吗？", "refusal"),
        ]:
            response = client.post(f"/v1/tenants/{tenant}/questions",
                                   json={"question": question}, headers=headers)
            assert response.status_code == 201, response.text
            answer = response.json()
            print({"question": question, "answer": answer["answer"],
                   "status": answer["status"], "generator": answer["generator"],
                   "sources": [item["document_title"] for item in answer["citations"]]}, flush=True)
            if expected == "refusal":
                assert answer["status"] == "insufficient_evidence"
                assert answer["citations"] == []
            else:
                assert answer["generator"] == "ollama"
                assert answer["status"] == "grounded" and "180" in answer["answer"]
                if expected == "multi":
                    assert "主管" in answer["answer"] or "经理" in answer["answer"]
                    assert {c["document_title"] for c in answer["citations"]} >= {
                        "Accommodation policy", "Travel approval",
                    }
