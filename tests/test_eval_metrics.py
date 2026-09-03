"""评测指标单元测试（不依赖 DB / LLM）。"""

from __future__ import annotations

from evals.metrics.generation import compute_generation_metrics
from evals.metrics.retrieval import compute_retrieval_metrics
from evals.schemas import MetricInput

def test_retrieval_perfect_hit():
    m = compute_retrieval_metrics(
        MetricInput(retrieval_gt=[[1, 2]], retrieval_ids=[1, 2, 99], generated_text="", generated_gt="")
    )
    assert m.precision == 2 / 3
    assert m.recall == 1.0
    assert m.mrr == 1.0

def test_retrieval_empty_preds():
    m = compute_retrieval_metrics(
        MetricInput(retrieval_gt=[[1]], retrieval_ids=[], generated_text="", generated_gt="")
    )
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.mrr == 0.0

def test_generation_overlap():
    m = compute_generation_metrics("访客中心在东门", "访客中心位于东门")
    assert m.rouge1 > 0.3
    assert m.bleu1 > 0

def test_passage_id_parse():
    from evals.corpus import eval_passage_document_id, parse_passage_id

    assert parse_passage_id(eval_passage_document_id(42)) == 42
    assert parse_passage_id("other_doc") is None

def test_load_campus_demo_dataset():
    from evals.datasets import load_dataset

    ds = load_dataset("campus_demo")
    assert ds.id == "campus_demo"
    assert len(ds.items) == 3
    assert len(ds.passages) == 3

def test_list_datasets():
    from evals.datasets import list_datasets

    ids = {d["id"] for d in list_datasets()}
    assert "campus_demo" in ids
    assert "hotpot" in ids
    assert "squad_v2" in ids

def test_validate_json_dataset():
    from evals.datasets import validate_json_dataset

    ds_id = validate_json_dataset(
        {
            "id": "test_set",
            "passages": [{"pid": 0, "text": "hello"}],
            "items": [{"qid": 0, "question": "q?", "pids": [0], "answer": "hello"}],
        }
    )
    assert ds_id == "test_set"


def test_hotpot_gold_paragraph_indices():
    from evals.datasets.hotpot import gold_paragraph_indices

    paragraphs = [
        {"title": "A", "text": "alpha"},
        {"title": "B", "text": "beta"},
        {"title": "C", "text": "gamma"},
    ]
    gold = gold_paragraph_indices(paragraphs, {"title": ["B", "C", "B"], "sent_id": [0, 1, 2]})
    assert gold == [1, 2]

def test_validate_json_dataset_unknown_pid():
    from evals.datasets import validate_json_dataset
    import pytest

    with pytest.raises(ValueError, match="未知 pid"):
        validate_json_dataset(
            {
                "id": "bad_set",
                "passages": [{"pid": 0, "text": "a"}],
                "items": [{"qid": 0, "question": "q?", "pids": [9], "answer": "x"}],
            }
        )
