from evals.cancel import EvalCancelled, check_stop
from evals.finalize import sample_row_to_result, summarize_sample_rows


def test_check_stop_raises_when_requested():
    check_stop(None)
    check_stop(lambda: False)
    try:
        check_stop(lambda: True)
        raise AssertionError("expected EvalCancelled")
    except EvalCancelled:
        pass


def test_summarize_sample_rows_squad_partial():
    rows = [
        {
            "qid": 0,
            "question": "q1",
            "reference": "France",
            "response": "France",
            "retrieval_ids": [1],
            "retrieval_gt": [1],
            "metrics": {
                "retrieval": {"precision": 1.0, "recall": 1.0, "ndcg3": 1.0, "ndcg10": 1.0, "mrr": 1.0, "map": 1.0},
                "squad": {"em": 1.0, "f1": 1.0, "span_hit": 1.0, "abstained": 0.0, "impossible": 0.0},
            },
            "latency_ms": 10,
            "error": None,
        },
        {
            "qid": 1,
            "question": "q2",
            "reference": "",
            "response": "unanswerable",
            "retrieval_ids": [2],
            "retrieval_gt": [2],
            "metrics": {
                "retrieval": {"precision": 1.0, "recall": 1.0, "ndcg3": 1.0, "ndcg10": 1.0, "mrr": 1.0, "map": 1.0},
                "squad": {"em": 1.0, "f1": 1.0, "span_hit": 1.0, "abstained": 1.0, "impossible": 1.0},
            },
            "latency_ms": 12,
            "error": None,
        },
    ]
    result = sample_row_to_result(rows[0])
    assert result.metrics.squad and result.metrics.squad.em == 1.0
    summary = summarize_sample_rows(rows)
    assert summary["sample_count"] == 2
    assert summary["squad_metrics"]["em"] == 1.0
    assert summary["squad_metrics"]["no_ans_count"] == 1
