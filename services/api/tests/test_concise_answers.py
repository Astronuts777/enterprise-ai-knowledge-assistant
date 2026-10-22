"""Answer selection must be concise without altering quoted facts or citation IDs."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_ai_providers import evidence
from test_questions_api import ADMIN_A, create_workspace, headers, upload_document

from enterprise_ai_api.ai_providers import LocalEvidenceAnswerProvider
from enterprise_ai_api.main import create_app
from enterprise_ai_api.models import DocumentChunk
from enterprise_ai_api.retrieval import select_local_answer_evidence

QUESTION = "远程办公每周最多几天，需要谁批准？"
POLICY = "员工每周最多可以远程办公两天。申请需提前一个工作日提交，并由直属主管批准。"
DEMO = (
    "# 演示员工远程办公制度（虚构资料）\n\n"
    "本文仅用于网站功能演示，不是真实企业制度，不包含真实员工信息。\n\n"
    "## 远程办公天数与审批\n\n" + POLICY + "\n\n"
    "## 信息安全要求\n\n远程办公时必须使用企业授权账号，不得将文件转发至个人邮箱。\n\n"
    "## 工作时间与协作\n\n远程办公期间应按约定参加团队会议。"
)


def passage(body, *, parent=None, position=1, semantic=None, lexical=1):
    item = parent or evidence(1)
    chunk = DocumentChunk(
        id=uuid4(), tenant_id=item.chunk.tenant_id, document_version_id=item.version.id,
        position=position, locator=f"paragraph:{position}", text=body,
    )
    return replace(item, chunk=chunk, excerpt=body, lexical_score=lexical, semantic_score=semantic)


def test_demo_answer_has_one_relevant_citation_and_exact_source_navigation(tmp_path):
    app = create_app("sqlite://", storage_root=str(tmp_path / "uploads"))
    with TestClient(app) as client:
        tenant, space = create_workspace(client, ADMIN_A, "Concise QA")
        upload_document(client, tenant, space, DEMO.encode(), publish=True)
        result = client.post(
            f"/v1/tenants/{tenant}/questions", json={"question": QUESTION},
            headers=headers(ADMIN_A),
        )
        assert result.status_code == 201
        answer = result.json()
        assert answer["status"] == "grounded"
        assert len(answer["answer"]) < 150
        assert POLICY in answer["answer"]
        assert "[1]" in answer["answer"] and "[2]" not in answer["answer"]
        assert len(answer["citations"]) == 1
        assert answer["citations"][0]["excerpt"] == POLICY
        assert answer["citations"][0]["locator"] == "paragraph:4"
        source = client.get(
            f"/v1/tenants/{tenant}/questions/{answer['id']}/citations/1/source",
            headers=headers(ADMIN_A),
        )
        assert source.status_code == 200
        assert source.json()["cited_chunk_id"] == answer["citations"][0]["document_chunk_id"]
        assert len(source.json()["chunks"]) == 8  # Full context remains available on demand.
        history = client.get(f"/v1/tenants/{tenant}/questions", headers=headers(ADMIN_A)).json()
        assert history[0]["answer"] == answer["answer"]
        assert history[0]["citations"] == answer["citations"]


@pytest.mark.parametrize("heading", ["# Remote work", "## 远程办公", "Remote work\n---", "---"])
def test_heading_alone_is_not_an_answer(heading):
    draft = LocalEvidenceAnswerProvider().generate(
        question="Remote work 远程办公", evidence=[passage(heading)],
    )
    assert draft.status == "insufficient_evidence"
    assert draft.evidence == []


def test_exact_duplicates_are_removed_without_changing_quote():
    first = passage(POLICY)
    draft = LocalEvidenceAnswerProvider().generate(
        question=QUESTION, evidence=[first, passage(POLICY), passage("## 远程办公")],
    )
    assert len(draft.evidence) == 1
    assert draft.text.count(POLICY) == 1
    assert draft.evidence[0].excerpt == POLICY


def test_complementary_documents_and_conflicting_rules_are_not_deduplicated():
    items = [passage("Remote work is allowed two days per week."),
             passage("Remote work is not allowed two days per week."),
             passage("Manager approval is required before submitting the request.")]
    draft = LocalEvidenceAnswerProvider().generate(
        question="Remote work days and manager approval?", evidence=items,
    )
    assert len(draft.evidence) == 3
    for item in items:
        assert item.excerpt in draft.text
    assert [f"[{i}]" in draft.text for i in range(1, 4)] == [True, True, True]


def test_case_sensitive_identifiers_are_not_treated_as_duplicate_facts():
    items = [passage("Project code is ABC."), passage("Project code is abc.")]
    selected = select_local_answer_evidence("Project code", items)
    assert len(selected) == 2


@pytest.mark.parametrize("condition", ["但试用期员工不适用。", "Except during probation."])
def test_adjacent_exception_without_keyword_overlap_is_kept(condition):
    main = passage(POLICY, position=4)
    exception = passage(condition, parent=main, position=5, lexical=0)
    unrelated = passage("公司餐厅周末关闭。", parent=main, position=3, lexical=0)
    draft = LocalEvidenceAnswerProvider().generate(
        question=QUESTION, evidence=[main, unrelated, exception],
    )
    assert [item.chunk.id for item in draft.evidence] == [main.chunk.id, exception.chunk.id]
    assert condition in draft.text
    assert unrelated.excerpt not in draft.text


def test_semantic_only_match_is_preserved_without_requiring_shared_words():
    item = passage("Employees can telecommute twice weekly.", semantic=0.9, lexical=0)
    draft = LocalEvidenceAnswerProvider().generate(question="异地办公天数", evidence=[item])
    assert draft.status == "grounded"
    assert draft.evidence == [item]


def test_metadata_only_document_lookup_keeps_body_not_heading():
    heading = passage("# Annual policy", position=1)
    body = passage("Employees receive twenty days of paid leave.", parent=heading,
                   position=2, lexical=0)
    selected = select_local_answer_evidence("policy-1.md", [heading, body])
    assert selected == [body]


def test_budget_selects_whole_passages_instead_of_cutting_conditions():
    first = passage("Remote work " + "requires approval. " * 36 + "Unless a waiver is granted.")
    second = passage("Remote work " + "requires review. " * 38 + "Only after training.")
    draft = LocalEvidenceAnswerProvider().generate(question="Remote work", evidence=[first, second])
    assert len(draft.evidence) == 1
    assert draft.evidence[0].excerpt in draft.text
    assert draft.evidence[0].excerpt.endswith(("granted.", "training."))


def test_candidate_input_is_not_mutated_and_unmatched_context_is_not_an_answer():
    item = passage("The cafeteria is closed.", lexical=0)
    original = [item]
    assert select_local_answer_evidence(QUESTION, original) == []
    assert original == [item]
