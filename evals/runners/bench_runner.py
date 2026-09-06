"""rag_bench 执行器：灌库 → 并行跑题 → 汇总指标。"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from config.settings import set_current_owner
from evals.cancel import EvalCancelled, check_stop
from evals.config import apply_config_overrides, eval_chat_model_kwargs
from evals.corpus import ingest_passages
from evals.datasets import load_dataset
from evals.metrics.aggregate import average_metrics, sample_metrics_to_dict
from evals.pipelines.agent import run_rag_agent
from evals.schemas import EvalConfig, SampleResult
from utils.vector_store import ChunkStore

logger = logging.getLogger(__name__)


def _make_runner(config: EvalConfig):
    """rag_bench / rag_quality 只跑产品 LangGraph（rag_agent）。"""
    layers = config.metric_layers
    kwargs = eval_chat_model_kwargs(config)

    from agents.graph import build_agent
    from evals.pipelines.agent import eval_system_prompt
    from tools.retrieval.doc_retrieval import doc_retrieval

    agent = build_agent(
        system_prompt=eval_system_prompt(layers),
        tools=[doc_retrieval],
        chat_model_kwargs=kwargs,
    )

    def _run(item, kb_id: int, owner: str) -> SampleResult:
        set_current_owner(owner)
        return run_rag_agent(
            item,
            kb_id=kb_id,
            agent=agent,
            metric_layers=layers,
            chat_model_kwargs=kwargs,
        )

    return _run


def run_bench(
    config: EvalConfig,
    *,
    on_progress: Callable[[int, int, dict], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_kb_created: Callable[[int, str], None] | None = None,
    qids: list[int] | None = None,
) -> tuple[list[SampleResult], dict]:
    """执行 rag_bench，返回 (逐题结果, 汇总指标)。"""
    if on_progress:
        on_progress(0, 0, {"phase": "loading"})
    check_stop(should_stop)

    dataset = load_dataset(config.dataset_id, sample_limit=config.sample_limit)
    items = list(dataset.items)
    if qids is not None:
        want = {int(q) for q in qids}
        items = [item for item in items if int(item.qid) in want]
        if not items:
            raise ValueError("没有匹配的失败题可重试")
    owner = config.owner or "eval"
    total = len(items)
    n_passages = len(dataset.passages)

    store = ChunkStore()
    store.init_schema()

    kb_template = None
    if config.kb_template_id:
        kb_template = store.get_knowledge_base(config.kb_template_id, owner=owner)

    kb_name = f"eval_{config.dataset_id}_{uuid.uuid4().hex[:6]}"
    eval_kb = store.create_knowledge_base(
        name=kb_name,
        description=f"评测临时库（{config.dataset_id}）。评测结束或中断后自动删除。",
        owner=owner,
        chunk_size=kb_template["chunk_size"] if kb_template else None,
        chunk_overlap=kb_template["chunk_overlap"] if kb_template else None,
        embedding_model_id=kb_template["embedding_model_id"] if kb_template else None,
        embedding_dim=kb_template["embedding_dim"] if kb_template else None,
        chunk_strategy=kb_template.get("chunk_strategy") if kb_template else None,
    )
    kb_id = eval_kb["id"]
    if on_kb_created:
        on_kb_created(kb_id, kb_name)

    results: list[SampleResult] = []
    try:
        with apply_config_overrides(config.config_overrides):
            set_current_owner(owner)

            last_ingest_report = [-1]

            def _ingest_prog(done: int, ingest_total: int) -> None:
                check_stop(should_stop)
                if not on_progress:
                    return
                step = max(1, ingest_total // 20) if ingest_total else 1
                if done not in (0, ingest_total) and done - last_ingest_report[0] < step:
                    return
                last_ingest_report[0] = done
                on_progress(
                    0,
                    total,
                    {
                        "phase": "ingest",
                        "ingest_finished": done,
                        "ingest_total": ingest_total,
                        "eval_kb_id": kb_id,
                        "eval_kb_name": kb_name,
                        "sample_count": 0,
                        "error_count": 0,
                    },
                )

            if on_progress:
                _ingest_prog(0, n_passages)
            ingest_passages(
                dataset.passages,
                kb_id=kb_id,
                owner=owner,
                kb_row=eval_kb,
                on_progress=_ingest_prog,
            )
            runner = _make_runner(config)
            if on_progress:
                on_progress(
                    0,
                    total,
                    {
                        "phase": "eval",
                        "ingest_finished": n_passages,
                        "ingest_total": n_passages,
                        "eval_kb_id": kb_id,
                        "eval_kb_name": kb_name,
                        "sample_count": 0,
                        "error_count": 0,
                    },
                )
            done = 0
            lock = threading.Lock()

            def _one(item):
                nonlocal done
                check_stop(should_stop)
                row = runner(item, kb_id, owner)
                sample_row = results_to_sample_rows([row])[0]
                if on_sample:
                    on_sample(sample_row)
                with lock:
                    done += 1
                    results.append(row)
                    summary = average_metrics(results)
                    summary["phase"] = "eval"
                    summary["ingest_finished"] = n_passages
                    summary["ingest_total"] = n_passages
                    summary["eval_kb_id"] = kb_id
                    summary["eval_kb_name"] = kb_name
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
    finally:
        try:
            store.delete_knowledge_base(kb_id, owner=owner)
        except Exception as exc:
            logger.warning("清理 eval KB 失败: %s", exc)

    summary = average_metrics(results)
    summary["phase"] = "done"
    return results, summary


def results_to_sample_rows(results: list[SampleResult]) -> list[dict]:
    return [
        {
            "qid": r.qid,
            "question": r.question,
            "reference": r.reference,
            "response": r.response,
            "retrieval_ids": r.retrieval_ids,
            "retrieval_gt": r.retrieval_gt,
            "metrics": sample_metrics_to_dict(r.metrics),
            "latency_ms": r.latency_ms,
            "error": r.error,
        }
        for r in results
    ]
