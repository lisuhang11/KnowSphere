"""意图识别评测指标单测。"""

from __future__ import annotations

from evals.metrics.intent import aggregate_intent_metrics, compute_intent_metrics
from evals.schemas import IntentMetrics, SampleMetrics, SampleResult


def test_compute_intent_metrics_exact_and_routing():
    m = compute_intent_metrics(
        intent_gt="kb_search",
        intent_pred="kb_search",
        question="年假几天",
        kb_selected=True,
        needs_retrieval_gt=True,
    )
    assert m.correct == 1.0
    assert m.routing_correct == 1.0

    m2 = compute_intent_metrics(
        intent_gt="follow_up",
        intent_pred="kb_search",
        question="它的维度呢",
        kb_selected=True,
        history_pairs=[{"query": "embedding", "answer": "bge"}],
        needs_retrieval_gt=False,
    )
    assert m2.correct == 0.0
    assert m2.routing_correct == 0.0  # pred 会检索，gt 不检索


def test_compute_intent_metrics_routing_can_match_despite_wrong_label():
    # greeting vs chitchat：都不检索，路由仍正确
    m = compute_intent_metrics(
        intent_gt="greeting",
        intent_pred="chitchat",
        question="你好",
        kb_selected=True,
        needs_retrieval_gt=False,
    )
    assert m.correct == 0.0
    assert m.routing_correct == 1.0


def test_aggregate_intent_macro_f1():
    def _row(qid: int, gt: str, pred: str, correct: float, routing: float) -> SampleResult:
        return SampleResult(
            qid=qid,
            question=f"q{qid}",
            reference=gt,
            response=pred,
            retrieval_ids=[],
            retrieval_gt=[],
            metrics=SampleMetrics(intent=IntentMetrics(correct=correct, routing_correct=routing)),
        )

    results = [
        _row(0, "kb_search", "kb_search", 1.0, 1.0),
        _row(1, "kb_search", "follow_up", 0.0, 0.0),
        _row(2, "greeting", "greeting", 1.0, 1.0),
        _row(3, "greeting", "greeting", 1.0, 1.0),
    ]
    summary = aggregate_intent_metrics(results)
    assert summary["sample_count"] == 4
    assert abs(summary["accuracy"] - 0.75) < 1e-6
    assert "kb_search" in summary["per_class"]
    assert "greeting" in summary["per_class"]
    assert 0.0 <= summary["macro_f1"] <= 1.0


def test_load_intent_demo_dataset():
    from evals.datasets import load_dataset, validate_json_dataset
    import json
    from pathlib import Path

    path = Path("evals/datasets/samples/intent_demo.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert validate_json_dataset(raw) == "intent_demo"
    ds = load_dataset("intent_demo")
    assert len(ds.items) >= 10
    assert all((it.meta or {}).get("intent_gt") for it in ds.items)
