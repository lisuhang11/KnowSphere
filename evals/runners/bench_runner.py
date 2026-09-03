"""rag_bench 执行器：灌库 → 并行跑题 → 汇总指标。"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from config.settings import set_current_owner
from evals.config import apply_config_overrides
from evals.corpus import ingest_passages
from evals.datasets import load_dataset
from evals.metrics.aggregate import average_metrics, sample_metrics_to_dict
from evals.pipelines.agent import run_rag_agent
from evals.pipelines.fixed import run_rag_fixed
from evals.schemas import EvalConfig, SampleResult
from utils.vector_store import ChunkStore

logger = logging.getLogger(__name__)


def _make_runner(config: EvalConfig):
    layers = config.metric_layers
    kwargs = {"temperature": 0, "extra_body": {"enable_thinking": False}}

    if config.pipeline_profile == "rag_agent":
        from agents.agent import build_agent
        from evals.pipelines.agent import eval_system_prompt
        from tools.retrieval.doc_retrieval import doc_retrieval

        agent = build_agent(
            system_prompt=eval_system_prompt(layers),
            tools=[doc_retrieval],
            chat_model_kwargs=kwargs,
        )

        def _run(item, kb_id: int, owner: str) -> SampleResult:
            set_current_owner(owner)
            return run_rag_agent(item, kb_id=kb_id, agent=agent, metric_layers=layers)

        return _run

    def _run_fixed(item, kb_id: int, owner: str) -> SampleResult:
        set_current_owner(owner)
        return run_rag_fixed(item, kb_id=kb_id, chat_model_kwargs=kwargs, metric_layers=layers)

    return _run_fixed


def run_bench(
    config: EvalConfig,
    *,
    on_progress: Callable[[int, int, dict], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
) -> tuple[list[SampleResult], dict]:
    """执行 rag_bench，返回 (逐题结果, 汇总指标)。"""
    dataset = load_dataset(config.dataset_id, sample_limit=config.sample_limit)
    owner = config.owner or "eval"
    task_owner = f"{owner}_{uuid.uuid4().hex[:8]}"

    store = ChunkStore()
    store.init_schema()

    kb_template = None
    if config.kb_template_id:
        kb_template = store.get_knowledge_base(config.kb_template_id, owner=owner)

    eval_kb = store.create_knowledge_base(
        name=f"eval_{config.dataset_id}_{uuid.uuid4().hex[:6]}",
        description=f"评测任务 KB ({config.dataset_id})",
        owner=task_owner,
        chunk_size=kb_template["chunk_size"] if kb_template else None,
        chunk_overlap=kb_template["chunk_overlap"] if kb_template else None,
        embedding_model_id=kb_template["embedding_model_id"] if kb_template else None,
        embedding_dim=kb_template["embedding_dim"] if kb_template else None,
        chunk_strategy=kb_template.get("chunk_strategy") if kb_template else None,
    )
    kb_id = eval_kb["id"]

    results: list[SampleResult] = []
    try:
        with apply_config_overrides(config.config_overrides):
            set_current_owner(task_owner)
            ingest_passages(dataset.passages, kb_id=kb_id, owner=task_owner, kb_row=eval_kb)
            runner = _make_runner(config)
            total = len(dataset.items)
            if on_progress:
                on_progress(0, total, {})
            done = 0
            lock = threading.Lock()

            def _one(item):
                nonlocal done
                row = runner(item, kb_id, task_owner)
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
                futures = [pool.submit(_one, item) for item in dataset.items]
                for fut in as_completed(futures):
                    fut.result()
            results.sort(key=lambda r: r.qid)
    finally:
        try:
            store.delete_knowledge_base(kb_id, owner=task_owner)
        except Exception as exc:
            logger.warning("清理 eval KB 失败: %s", exc)

    return results, average_metrics(results)


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
