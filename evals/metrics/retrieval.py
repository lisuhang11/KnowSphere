"""检索指标：Precision / Recall / NDCG / MRR / MAP。"""

from __future__ import annotations

import math

from evals.schemas import MetricInput, RetrievalMetrics


def compute_retrieval_metrics(inp: MetricInput) -> RetrievalMetrics:
    gt_lists = inp.retrieval_gt or [[]]
    gt_set: set[int] = set()
    for group in gt_lists:
        gt_set.update(group)
    preds = inp.retrieval_ids or []
    if not gt_set:
        return RetrievalMetrics()
    return RetrievalMetrics(
        precision=_precision(preds, gt_set),
        recall=_recall(preds, gt_set),
        ndcg3=_ndcg(preds, gt_set, 3),
        ndcg10=_ndcg(preds, gt_set, 10),
        mrr=_mrr(preds, gt_set),
        map=_map_score(preds, gt_set),
    )


def _precision(preds: list[int], gt_set: set[int]) -> float:
    if not preds:
        return 0.0
    hits = sum(1 for pid in preds if pid in gt_set)
    return hits / len(preds)


def _recall(preds: list[int], gt_set: set[int]) -> float:
    if not gt_set:
        return 0.0
    pred_set = set(preds)
    hits = sum(1 for pid in gt_set if pid in pred_set)
    return hits / len(gt_set)


def _ndcg(preds: list[int], gt_set: set[int], k: int) -> float:
    if not gt_set or not preds:
        return 0.0
    dcg = 0.0
    for i, pid in enumerate(preds[:k]):
        rel = 1.0 if pid in gt_set else 0.0
        dcg += (2**rel - 1) / math.log2(i + 2)
    ideal = min(len(gt_set), k)
    idcg = sum((2**1 - 1) / math.log2(i + 2) for i in range(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def _mrr(preds: list[int], gt_set: set[int]) -> float:
    for i, pid in enumerate(preds):
        if pid in gt_set:
            return 1.0 / (i + 1)
    return 0.0


def _map_score(preds: list[int], gt_set: set[int]) -> float:
    if not gt_set:
        return 0.0
    hits = 0
    prec_sum = 0.0
    for i, pid in enumerate(preds):
        if pid in gt_set:
            hits += 1
            prec_sum += hits / (i + 1)
    return prec_sum / len(gt_set)
