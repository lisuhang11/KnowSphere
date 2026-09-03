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
    on_phase: Callable[[str, int], None] | None = None,
) -> tuple[dict[str, float], list[dict], dict[int, dict[str, float]]]:
    if not rows:
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")

    if on_phase:
        on_phase("ragas", len(rows))

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
    on_phase: Callable[[str, int], None] | None = None,
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
        except Exception as exc:
            return qid, {"qid": qid, "error": str(exc), "user_input": item.get("question", "")}, 0
        finally:
            cleanup_question(owner, kb_id)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, qid, item): (qid, item) for qid, item in indexed}
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
    on_phase: Callable[[str, int], None] | None = None,
) -> tuple[dict[str, float], list[dict], list[dict]]:
    """JSON/Parquet 数据集 + Agent + RAGAS。"""
    dataset = load_dataset(config.dataset_id, sample_limit=config.sample_limit)
    owner = config.owner or "eval"
    task_owner = f"{owner}_{uuid.uuid4().hex[:8]}"

    store = ChunkStore()
    store.init_schema()

    eval_kb = store.create_knowledge_base(
        name=f"ragas_{config.dataset_id}_{uuid.uuid4().hex[:6]}",
        description=f"RAGAS 评测 KB ({config.dataset_id})",
        owner=task_owner,
    )
    kb_id = eval_kb["id"]

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
        set_current_owner(task_owner)
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
        except Exception as exc:
            return qid, {"qid": qid, "error": str(exc), "user_input": item.question}

    try:
        ingest_passages(dataset.passages, kb_id=kb_id, owner=task_owner, kb_row=eval_kb)

        if on_progress:
            on_progress(0, len(indexed))

        with ThreadPoolExecutor(max_workers=max(1, config.workers)) as pool:
            futures = {pool.submit(_run_one, qid, item): (qid, item) for qid, item in indexed}
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
    finally:
        try:
            store.delete_knowledge_base(kb_id, owner=task_owner)
        except Exception:
            pass

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
