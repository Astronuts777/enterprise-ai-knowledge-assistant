"""Opt-in synthetic benchmark; never connects to a business database or model."""

import json
import os
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from sqlalchemy import update
from test_retrieval_streaming import corpus  # noqa: F401

from enterprise_ai_api import retrieval
from enterprise_ai_api.models import DocumentChunk

BASELINE_REF = "2db13070e725fccf17dc07797104a2587d53f46b"


@pytest.mark.skipif(
    os.getenv("BENCHMARK_RETRIEVAL") != "1"
    or os.getenv("RETRIEVAL_TEST_DATABASE_URL", "sqlite://") != "sqlite://",
    reason="Opt-in benchmark requires isolated SQLite",
)
def test_encrypted_corpus_performance_and_equivalence(corpus):  # noqa: F811
    app, _, tenant, space, _, first_id = corpus
    assert app.state.engine.dialect.name == "sqlite", "Benchmark requires isolated SQLite"
    # The immutable, previously deployed implementation is the comparison oracle.
    # This reads local Git only; no network, credentials, real documents or models.
    root = Path(__file__).resolve().parents[3]
    source = subprocess.check_output([
        "git", "show", f"{BASELINE_REF}:services/api/src/enterprise_ai_api/retrieval.py",
    ], cwd=root, text=True)
    baseline = types.ModuleType("_retrieval_benchmark_baseline")
    sys.modules[baseline.__name__] = baseline
    try:
        exec(compile(source, "<baseline retrieval>", "exec"), baseline.__dict__)
        body = "Routine procedures require approval. Archive retention policy. " * 50
        vector = [0.3] + [0.01] * 767
        with app.state.session_factory() as session:
            session.execute(update(DocumentChunk).where(DocumentChunk.tenant_id == tenant).values(
                text=body, embedding=json.dumps(vector), embedding_model="benchmark-768",
            ))
            session.execute(update(DocumentChunk).where(DocumentChunk.id == first_id).values(
                text="ZX-987 approved. " + body,
            ))
            session.commit()

        def fingerprint(result):
            return [(
                item.chunk.id, item.document.id, item.version.id, item.score,
                item.lexical_score, item.semantic_score, item.excerpt, item.classification,
            ) for item in result]

        for name, question, semantic in [
            ("keyword_sparse", "ZX-987", False),
            ("keyword_dense", "Routine procedures", False),
            ("hybrid_sparse", "ZX-987", True),
            ("hybrid_dense", "Routine procedures", True),
        ]:
            options = dict(tenant_id=tenant, space_ids=[space], question=question)
            if semantic:
                # Orthogonal enough to remain below the pure semantic threshold.
                options.update(query_embedding=[0.0, 1.0] + [0.0] * 766,
                               embedding_model="benchmark-768")
            timings = {"baseline": [], "optimized": []}
            expected = None
            for repeat in range(3):
                order = [("baseline", baseline), ("optimized", retrieval)]
                if repeat % 2:
                    order.reverse()
                for label, implementation in order:
                    with app.state.session_factory() as session:
                        started = time.perf_counter()
                        result = implementation.search_published_knowledge(session, **options)
                        timings[label].append(time.perf_counter() - started)
                        actual = fingerprint(result)
                        assert actual
                        if expected is None:
                            expected = actual
                        assert actual == expected
            old, new = (statistics.median(timings[key]) for key in ("baseline", "optimized"))
            print(json.dumps(dict(
                scenario=name, chunks=5011, body_chars=len(body), vector_dimensions=768,
                repeats=3, baseline_seconds=round(old, 4), optimized_seconds=round(new, 4),
                speedup=round(old / new, 2), identical_evidence=True,
            )))
        # Timing isn't a flaky CI assertion. Regular regressions enforce bounded
        # hydration/excerpts and skip-unneeded-vector work deterministically.
    finally:
        sys.modules.pop(baseline.__name__, None)
