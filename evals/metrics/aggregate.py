"""单题与任务级指标汇聚。"""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from evals.metrics.generation import compute_generation_metrics
from evals.metrics.intent import aggregate_intent_metrics
from evals.metrics.retrieval import compute_retrieval_metrics
from evals.schemas import (
    GenerationMetrics,
    MetricInput,
    RetrievalMetrics,
    SampleMetrics,
    SampleResult,
)


def compute_sample_metrics(
    inp: MetricInput,
    *,
    layers: list[str] | None = None,
) -> SampleMetrics:
    layers = layers or ["retrieval", "generation"]
    out = SampleMetrics()
    if "retrieval" in layers:
        out.retrieval = compute_retrieval_metrics(inp)
    if "generation" in layers:
        out.generation = compute_generation_metrics(inp.generated_gt, inp.generated_text)
    return out


def _avg_dataclass(items: list[Any], cls: type) -> dict[str, float]:
    if not items:
        return {f.name: 0.0 for f in fields(cls)}
    sums = {f.name: 0.0 for f in fields(cls)}
    for item in items:
        for f in fields(cls):
            sums[f.name] += float(getattr(item, f.name))
    n = len(items)
    return {k: v / n for k, v in sums.items()}


def average_metrics(results: list[SampleResult]) -> dict[str, Any]:
    ok = [r for r in results if not r.error]
    retrieval = [r.metrics.retrieval for r in ok if r.metrics.retrieval]
    generation = [r.metrics.generation for r in ok if r.metrics.generation]
    summary: dict[str, Any] = {"sample_count": len(ok), "error_count": len(results) - len(ok)}
    if retrieval:
        summary["retrieval_metrics"] = _avg_dataclass(retrieval, RetrievalMetrics)
    if generation:
        summary["generation_metrics"] = _avg_dataclass(generation, GenerationMetrics)
    ragas_rows = [r.metrics.ragas for r in ok if r.metrics.ragas]
    if ragas_rows:
        keys = ragas_rows[0].keys()
        summary["ragas_metrics"] = {k: sum(row[k] for row in ragas_rows) / len(ragas_rows) for k in keys}
    if any(r.metrics.intent for r in ok):
        intent_summary = aggregate_intent_metrics(results)
        # 对外统一字段名（accuracy = mean(correct)）
        summary["intent_metrics"] = {
            "accuracy": intent_summary["accuracy"],
            "routing_accuracy": intent_summary["routing_accuracy"],
            "macro_f1": intent_summary["macro_f1"],
            "per_class": intent_summary["per_class"],
        }
    return summary


def sample_metrics_to_dict(m: SampleMetrics) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if m.retrieval:
        out["retrieval"] = asdict(m.retrieval)
    if m.generation:
        out["generation"] = asdict(m.generation)
    if m.ragas:
        out["ragas"] = dict(m.ragas)
    if m.intent:
        out["intent"] = asdict(m.intent)
    return out
