"""RAGAS 评测：HotpotQA 或 JSON/Parquet 数据集 + Agent。"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ragas import EvaluationDataset, RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from agents.agent import build_agent
from config.settings import set_current_owner, settings
from evals.cancel import EvalCancelled, check_stop
from evals.corpus import ingest_passages
from evals.datasets import load_dataset
from evals.hotpot import cleanup_question, ingest_question, load_hotpot_sample
from evals.pipelines.agent import EVAL_SYSTEM_PROMPT, _extract
from evals.schemas import EvalConfig, QAPair
from models import create_chat_model, create_embeddings
from tools.retrieval.doc_retrieval import doc_retrieval
from utils.vector_store import ChunkStore

_LLM_KWARGS = {"temperature": 0, "extra_body": {"enable_thinking": False}}
_METRICS = [faithfulness, answer_relevancy, context_precision, context_recall]
_METRIC_KEYS = {m.name for m in _METRICS}


def _to_sample_row(
    qid: int,
    item: dict | QAPair,
    row: dict,
    ragas_scores: dict[str, float] | None = None,
) -> dict:
    if isinstance(item, QAPair):
        question = item.question
        reference = item.answer
    else:
        question = item.get("question") or ""
        reference = item.get("answer") or ""
    return {
        "qid": qid,
        "question": row.get("user_input") or question,
        "reference": row.get("reference") or reference,
        "response": row.get("response") or "",
        "retrieval_ids": [],
        "retrieval_gt": [],
        "metrics": {"ragas": ragas_scores or {}},
        "latency_ms": row.get("latency_ms"),
        "error": row.get("error"),
    }


def _run_ragas_batch(
    rows: list[dict],
    *,
    on_phase: Callable[[str, int, int | None], None] | None = None,
) -> tuple[dict[str, float], list[dict], dict[int, dict[str, float]]]:
    if not rows:
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")

    if on_phase:
        on_phase("ragas", len(rows), None)

    run_config = RunConfig(timeout=480, max_retries=10, max_wait=120, max_workers=4)
    result = evaluate(
        EvaluationDataset.from_list(rows),
        metrics=_METRICS,
        llm=LangchainLLMWrapper(create_chat_model(**_LLM_KWARGS), run_config=run_config),
        embeddings=LangchainEmbeddingsWrapper(create_embeddings()),
        run_config=run_config,
    )
    df = result.to_pandas()
    metric_cols = [c for c in df.columns if c in _METRIC_KEYS]
    summary = df[metric_cols].mean().round(4).to_dict()
    detail = df.to_dict(orient="records")

    ragas_by_qid: dict[int, dict[str, float]] = {}
    for i, record in enumerate(detail):
        qid = rows[i].get("qid", i)
        ragas_by_qid[qid] = {k: float(record[k]) for k in metric_cols if k in record}

    return summary, detail, ragas_by_qid


def run_ragas_eval(
    *,
    n: int = 50,
    seed: int = 42,
    split: str = "validation",
    workers: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
    on_phase: Callable[[str, int, int | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict[str, float], list[dict], list[dict]]:
    """HotpotQA + Agent + RAGAS（原 CLI 路径）。"""
    ChunkStore().init_schema()
    items = load_hotpot_sample(n=n, split=split, seed=seed)
    indexed = list(enumerate(items))
    agent = build_agent(
        system_prompt=EVAL_SYSTEM_PROMPT,
        tools=[doc_retrieval],
        chat_model_kwargs=_LLM_KWARGS,
    )

    rows: list[dict] = []
    row_by_qid: dict[int, dict] = {}
    done = 0
    lock = threading.Lock()

    def _run_one(qid: int, item: dict) -> tuple[int, dict, int]:
        check_stop(should_stop)
        owner = f"hotpot_{item['id']}"
        set_current_owner(owner)
        kb_id: int | None = None
        try:
            kb_id, n_chunks = ingest_question(item, owner)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": item["question"]}]},
                config={
                    "configurable": {"kb_ids": [kb_id]},
                    "recursion_limit": settings.agent_max_steps,
                },
            )
            answer, sources = _extract(result)
            contexts = [
                f"[{s['file_name']}#chunk{s['chunk_index']}] {s['snippet']}"
                for s in sources
            ]
            row = {
                "qid": qid,
                "user_input": item["question"],
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": item["answer"],
                "type": item["type"],
            }
            return qid, row, n_chunks
        except EvalCancelled:
            raise
        except Exception as exc:
            return qid, {"qid": qid, "error": str(exc), "user_input": item.get("question", "")}, 0
        finally:
            cleanup_question(owner, kb_id)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, qid, item): (qid, item) for qid, item in indexed}
        try:
            for fut in as_completed(futures):
                qid, item = futures[fut]
                qid, row, _ = fut.result()
                with lock:
                    done += 1
                    row_by_qid[qid] = row
                    if on_progress:
                        on_progress(done, len(items))
                    sample = _to_sample_row(qid, item, row)
                    if on_sample:
                        on_sample(sample)
                    if not row.get("error"):
                        rows.append(row)
        except EvalCancelled:
            for fut in futures:
                fut.cancel()
            raise

    check_stop(should_stop)
    if not rows:
        samples = [_to_sample_row(qid, item, row_by_qid.get(qid, {})) for qid, item in indexed]
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")

    summary, detail, ragas_by_qid = _run_ragas_batch(rows, on_phase=on_phase)

    samples = []
    for qid, item in indexed:
        row = row_by_qid.get(qid, {})
        samples.append(_to_sample_row(qid, item, row, ragas_by_qid.get(qid)))
        if on_sample:
            on_sample(samples[-1])

    return summary, detail, samples


def run_ragas_eval_dataset(
    config: EvalConfig,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
    on_phase: Callable[[str, int, int | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_kb_created: Callable[[int, str], None] | None = None,
) -> tuple[dict[str, float], list[dict], list[dict]]:
    """JSON/Parquet 数据集 + Agent + RAGAS。"""
    dataset = load_dataset(config.dataset_id, sample_limit=config.sample_limit)
    owner = config.owner or "eval"

    store = ChunkStore()
    store.init_schema()

    kb_name = f"ragas_{config.dataset_id}_{uuid.uuid4().hex[:6]}"
    eval_kb = store.create_knowledge_base(
        name=kb_name,
        description=f"RAGAS 评测临时库（{config.dataset_id}）。评测结束或中断后自动删除。",
        owner=owner,
    )
    kb_id = eval_kb["id"]
    if on_kb_created:
        on_kb_created(kb_id, kb_name)

    agent = build_agent(
        system_prompt=EVAL_SYSTEM_PROMPT,
        tools=[doc_retrieval],
        chat_model_kwargs=_LLM_KWARGS,
    )

    indexed = list(enumerate(dataset.items))
    rows: list[dict] = []
    row_by_qid: dict[int, dict] = {}
    done = 0
    lock = threading.Lock()

    def _run_one(qid: int, item: QAPair) -> tuple[int, dict]:
        check_stop(should_stop)
        set_current_owner(owner)
        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": item.question}]},
                config={
                    "configurable": {"kb_ids": [kb_id]},
                    "recursion_limit": settings.agent_max_steps,
                },
            )
            answer, sources = _extract(result)
            contexts = [
                f"[{s['file_name']}#chunk{s['chunk_index']}] {s['snippet']}"
                for s in sources
            ]
            return qid, {
                "qid": qid,
                "user_input": item.question,
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": item.answer,
            }
        except EvalCancelled:
            raise
        except Exception as exc:
            return qid, {"qid": qid, "error": str(exc), "user_input": item.question}

    try:
        n_passages = len(dataset.passages)
        total_items = len(indexed)

        last_ingest_report = [-1]

        def _on_ingest(done: int, ingest_total: int) -> None:
            check_stop(should_stop)
            step = max(1, ingest_total // 20) if ingest_total else 1
            if done not in (0, ingest_total) and done - last_ingest_report[0] < step:
                return
            last_ingest_report[0] = done
            if on_phase:
                on_phase("ingest", ingest_total, done)
            if on_progress:
                on_progress(done, max(ingest_total, 1))

        if on_phase:
            on_phase("ingest", n_passages, 0)
        ingest_passages(
            dataset.passages,
            kb_id=kb_id,
            owner=owner,
            kb_row=eval_kb,
            on_progress=_on_ingest,
        )

        if on_phase:
            on_phase("agent", total_items, 0)
        if on_progress:
            on_progress(0, total_items)

        with ThreadPoolExecutor(max_workers=max(1, config.workers)) as pool:
            futures = {pool.submit(_run_one, qid, item): (qid, item) for qid, item in indexed}
            try:
                for fut in as_completed(futures):
                    qid, item = futures[fut]
                    qid, row = fut.result()
                    with lock:
                        done += 1
                        row_by_qid[qid] = row
                        if on_progress:
                            on_progress(done, len(indexed))
                        sample = _to_sample_row(qid, item, row)
                        if on_sample:
                            on_sample(sample)
                        if not row.get("error"):
                            rows.append(row)
            except EvalCancelled:
                for fut in futures:
                    fut.cancel()
                raise
    finally:
        try:
            store.delete_knowledge_base(kb_id, owner=owner)
        except Exception:
            pass

    check_stop(should_stop)
    if not rows:
        samples = [_to_sample_row(qid, item, row_by_qid.get(qid, {})) for qid, item in indexed]
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")

    summary, detail, ragas_by_qid = _run_ragas_batch(rows, on_phase=on_phase)

    samples = []
    for qid, item in indexed:
        row = row_by_qid.get(qid, {})
        samples.append(_to_sample_row(qid, item, row, ragas_by_qid.get(qid)))
        if on_sample:
            on_sample(samples[-1])

    return summary, detail, samples
