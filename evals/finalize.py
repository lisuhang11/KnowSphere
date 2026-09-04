"""从已落库的逐题明细汇总评测结果。"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from evals.metrics.aggregate import average_metrics
from evals.schemas import (
    GenerationMetrics,
    IntentMetrics,
    RetrievalMetrics,
    SampleMetrics,
    SampleResult,
    SquadMetrics,
)


class NoEvalSamples(ValueError):
    """还没有已完成的题目，无法产出结果。"""


def _dataclass_from_dict(cls: type, data: Any):
    if not isinstance(data, dict):
        return None
    names = {f.name for f in fields(cls)}
    payload = {k: v for k, v in data.items() if k in names}
    if not payload:
        return None
    return cls(**payload)


def sample_row_to_result(row: dict[str, Any]) -> SampleResult:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    sm = SampleMetrics()
    if metrics.get("retrieval"):
        sm.retrieval = _dataclass_from_dict(RetrievalMetrics, metrics["retrieval"])
    if metrics.get("generation"):
        sm.generation = _dataclass_from_dict(GenerationMetrics, metrics["generation"])
    if metrics.get("squad"):
        sm.squad = _dataclass_from_dict(SquadMetrics, metrics["squad"])
    if metrics.get("intent"):
        sm.intent = _dataclass_from_dict(IntentMetrics, metrics["intent"])
    ragas = metrics.get("ragas")
    if isinstance(ragas, dict) and ragas:
        sm.ragas = {k: float(v) for k, v in ragas.items() if isinstance(v, (int, float))}
    return SampleResult(
        qid=int(row.get("qid") or 0),
        question=str(row.get("question") or ""),
        reference=str(row.get("reference") or ""),
        response=str(row.get("response") or ""),
        retrieval_ids=list(row.get("retrieval_ids") or []),
        retrieval_gt=list(row.get("retrieval_gt") or []),
        metrics=sm,
        latency_ms=int(row.get("latency_ms") or 0),
        error=row.get("error"),
    )


def summarize_sample_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results = [sample_row_to_result(row) for row in rows]
    return average_metrics(results)


def mark_result_summary(
    summary: dict[str, Any],
    *,
    partial: bool,
    planned_total: int,
    prev: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(summary)
    out["phase"] = "done"
    out["result_ready"] = True
    out["partial"] = bool(partial)
    out["planned_total"] = int(planned_total)
    prev = prev or {}
    for key in ("eval_kb_id", "eval_kb_name", "ingest_finished", "ingest_total"):
        if key in prev and key not in out:
            out[key] = prev[key]
    return out


def finalize_task_results(task_id: str, *, partial: bool | None = None) -> dict[str, Any]:
    """按已落库样本产出评测结果。无样本时抛 NoEvalSamples。"""
    from utils.eval_store import EvalStore

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise FileNotFoundError("任务不存在")
    rows = store.list_all_samples(task_id)
    if not rows:
        raise NoEvalSamples("尚无已完成的题目，无法产出结果")
    planned = int(task.get("total") or 0)
    if planned <= 0:
        planned = len(rows)
    is_partial = len(rows) < planned
    if partial is True:
        is_partial = True
    prev = task.get("metric_summary") if isinstance(task.get("metric_summary"), dict) else {}
    summary = mark_result_summary(
        summarize_sample_rows(rows),
        partial=is_partial,
        planned_total=planned,
        prev=prev,
    )
    store.update_task(
        task_id,
        status="success",
        metric_summary=summary,
        finished=len(rows),
        total=max(planned, len(rows)),
        finished_at=True,
        clear_err_msg=True,
    )
    updated = store.get_task(task_id)
    if not updated:
        raise FileNotFoundError("任务不存在")
    return updated
