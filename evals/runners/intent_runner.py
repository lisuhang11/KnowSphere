"""intent_bench 执行器：无需灌库，并行跑 query_understand。"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from config.settings import set_current_owner
from evals.cancel import EvalCancelled, check_stop
from evals.config import apply_config_overrides
from evals.datasets import load_dataset
from evals.metrics.aggregate import average_metrics, sample_metrics_to_dict
from evals.pipelines.intent import run_intent_item
from evals.schemas import EvalConfig, SampleResult

logger = logging.getLogger(__name__)


def run_intent_bench(
    config: EvalConfig,
    *,
    on_progress: Callable[[int, int, dict], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    qids: list[int] | None = None,
) -> tuple[list[SampleResult], dict]:
    """执行意图识别评测，返回 (逐题结果, 汇总指标)。"""
    dataset = load_dataset(config.dataset_id, sample_limit=config.sample_limit)
    items = list(dataset.items)
    if qids is not None:
        want = {int(q) for q in qids}
        items = [item for item in items if int(item.qid) in want]
        if not items:
            raise ValueError("没有匹配的失败题可重试")
    missing = [it.qid for it in items if not (it.meta or {}).get("intent_gt")]
    if missing:
        raise ValueError(
            f"intent_bench 要求每题含 meta.intent_gt，缺失 qid: {missing[:10]}"
            + ("..." if len(missing) > 10 else "")
        )

    owner = config.owner or "eval"
    results: list[SampleResult] = []
    overrides = dict(config.config_overrides or {})
    overrides.setdefault("enable_rewrite", True)

    with apply_config_overrides(overrides):
        set_current_owner(owner)
        total = len(items)
        if on_progress:
            on_progress(0, total, {})
        done = 0
        lock = threading.Lock()

        def _one(item) -> SampleResult:
            nonlocal done
            check_stop(should_stop)
            set_current_owner(owner)
            row = run_intent_item(item, chat_model_id=config.chat_model_id)
            sample_row = results_to_sample_rows([row])[0]
            if on_sample:
                on_sample(sample_row)
            with lock:
                done += 1
                results.append(row)
                summary = average_metrics(results)
                if on_progress:
                    on_progress(done, total, summary)
            return row

        workers = max(1, min(config.workers, total or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, item) for item in items]
            try:
                for fut in as_completed(futures):
                    fut.result()
            except EvalCancelled:
                for fut in futures:
                    fut.cancel()
                raise
        results.sort(key=lambda r: r.qid)

    return results, average_metrics(results)


def results_to_sample_rows(results: list[SampleResult]) -> list[dict]:
    rows: list[dict] = []
    for r in results:
        metrics = sample_metrics_to_dict(r.metrics)
        if "intent" in metrics:
            metrics["intent"] = {
                **metrics["intent"],
                "pred_intent": r.response,
                "intent_gt": r.reference,
                **{k: v for k, v in (r.details or {}).items() if k.startswith("rewrite") or k == "kb_selected"},
            }
        rows.append(
            {
                "qid": r.qid,
                "question": r.question,
                "reference": r.reference,
                "response": r.response,
                "retrieval_ids": r.retrieval_ids,
                "retrieval_gt": r.retrieval_gt,
                "metrics": metrics,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
        )
    return rows
