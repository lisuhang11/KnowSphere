"""意图识别指标：Accuracy / Routing Accuracy / Macro-F1。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from evals.schemas import IntentMetrics, SampleResult
from schemas.query import needs_retrieval


def compute_intent_metrics(
    *,
    intent_gt: str,
    intent_pred: str,
    question: str,
    kb_selected: bool,
    history_pairs: list[dict[str, str]] | None = None,
    needs_retrieval_gt: bool | None = None,
    has_images: bool = False,
    has_attachments: bool = False,
) -> IntentMetrics:
    """计算单题意图指标。"""
    gt = (intent_gt or "").strip()
    pred = (intent_pred or "").strip()
    if needs_retrieval_gt is None:
        needs_gt = needs_retrieval(gt or None, kb_selected)
    else:
        needs_gt = bool(needs_retrieval_gt)
    needs_pred = needs_retrieval(pred or None, kb_selected)
    return IntentMetrics(
        correct=1.0 if gt and pred == gt else 0.0,
        routing_correct=1.0 if needs_pred == needs_gt else 0.0,
    )


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def aggregate_intent_metrics(results: list[SampleResult]) -> dict[str, Any]:
    """从逐题结果汇总意图指标（含 Macro-F1 与分 intent 明细）。"""
    ok = [r for r in results if not r.error and r.metrics.intent]
    if not ok:
        return {
            "accuracy": 0.0,
            "routing_accuracy": 0.0,
            "macro_f1": 0.0,
            "sample_count": 0,
            "per_class": {},
        }

    accuracy = sum(r.metrics.intent.correct for r in ok) / len(ok)
    routing = sum(r.metrics.intent.routing_correct for r in ok) / len(ok)

    # reference = intent_gt, response = intent_pred（intent_bench 约定）
    labels: set[str] = set()
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    support: dict[str, int] = defaultdict(int)

    for r in ok:
        gt = (r.reference or "").strip()
        pred = (r.response or "").strip()
        if gt:
            labels.add(gt)
            support[gt] += 1
        if pred:
            labels.add(pred)
        if gt and pred == gt:
            tp[gt] += 1
        else:
            if pred:
                fp[pred] += 1
            if gt:
                fn[gt] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    f1s: list[float] = []
    for label in sorted(labels):
        p = tp[label] / (tp[label] + fp[label]) if (tp[label] + fp[label]) else 0.0
        rec = tp[label] / (tp[label] + fn[label]) if (tp[label] + fn[label]) else 0.0
        score = _f1(p, rec)
        per_class[label] = {
            "precision": p,
            "recall": rec,
            "f1": score,
            "support": support.get(label, 0),
        }
        # Macro-F1：仅对 gold 中出现过的类求平均
        if support.get(label, 0) > 0:
            f1s.append(score)

    return {
        "accuracy": accuracy,
        "routing_accuracy": routing,
        "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
        "sample_count": len(ok),
        "per_class": per_class,
    }
