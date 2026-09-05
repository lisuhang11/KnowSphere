import time

import pytest

from evals.failed import is_retryable_sample, retryable_qids
from evals.runners.ragas_runner import (
    _ragas_is_finished,
    _run_with_timeout,
    select_dataset_retry_items,
)
from evals.schemas import QAPair


def test_retryable_samples_errors_and_empty_rag_quality():
    samples = [
        {"qid": 0, "error": "429", "response": ""},
        {"qid": 1, "error": None, "response": "ok"},
        {"qid": 2, "error": "", "response": "   "},
        {"qid": 3, "error": None, "response": "answered"},
    ]
    assert is_retryable_sample(samples[0], suite="rag_quality")
    assert not is_retryable_sample(samples[1], suite="rag_quality")
    assert is_retryable_sample(samples[2], suite="rag_quality")
    assert retryable_qids(samples, suite="rag_quality") == [0, 2]


def test_retryable_samples_bench_only_errors():
    samples = [
        {"qid": 10, "error": "timeout", "response": ""},
        {"qid": 11, "error": None, "response": ""},
        {"qid": 12, "error": "boom", "response": "partial"},
    ]
    assert retryable_qids(samples, suite="rag_bench") == [10, 12]
    assert retryable_qids(samples, suite="intent_bench") == [10, 12]


def test_select_dataset_retry_items_matches_qid_or_index():
    items = [
        QAPair(qid=10, question="a", pids=[], passages=[], answer=""),
        QAPair(qid=20, question="b", pids=[], passages=[], answer=""),
    ]
    by_qid = select_dataset_retry_items(items, [20])
    assert [q for q, _ in by_qid] == [20]
    by_idx = select_dataset_retry_items(items, [0])
    assert [q for q, it in by_idx] == [0]
    assert by_idx[0][1].question == "a"


def test_ragas_is_finished_never_blocks_on_vendor_finish_reason():
    assert _ragas_is_finished(None) is True


def test_run_with_timeout_raises_on_hang():
    def _hang() -> dict:
        time.sleep(2)
        return {}

    with pytest.raises(TimeoutError, match="仍未返回"):
        _run_with_timeout(_hang, 0.2)
