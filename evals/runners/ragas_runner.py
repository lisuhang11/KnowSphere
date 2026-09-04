"""RAGAS 评测：HotpotQA 或 JSON/Parquet 数据集 + Agent。"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from ragas import EvaluationDataset, RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

from agents.agent import build_agent
from config.settings import set_current_owner
from evals.cancel import EvalCancelled, check_stop
from evals.config import eval_chat_model_kwargs, eval_embedding_kwargs, eval_invoke_config
from evals.corpus import ingest_passages
from evals.datasets import load_dataset
from evals.hotpot import cleanup_question, ingest_question, load_hotpot_sample
from evals.pipelines.agent import EVAL_SYSTEM_PROMPT, _extract
from evals.retry import call_with_tpm_retry
from evals.schemas import EvalConfig, QAPair
from models import create_chat_model, create_embeddings
from tools.retrieval.doc_retrieval import doc_retrieval
from utils.vector_store import ChunkStore

logger = logging.getLogger(__name__)

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


def scorable_ragas_rows(rows: list[dict]) -> list[dict]:
    """只保留 Agent 跑通且有回答的题；运行出错 / 空答不进 RAGAS。"""
    out: list[dict] = []
    for row in rows:
        if row.get("error"):
            continue
        question = str(row.get("user_input") or "").strip()
        answer = str(row.get("response") or "").strip()
        if not question or not answer:
            continue
        payload = dict(row)
        ctx = payload.get("retrieved_contexts")
        if not isinstance(ctx, list):
            payload["retrieved_contexts"] = []
        payload["reference"] = str(payload.get("reference") or "")
        out.append(payload)
    return out


def _finite_scores(record: dict) -> dict[str, float]:
    scores: dict[str, float] = {}
    for key in _METRIC_KEYS:
        if key not in record:
            continue
        try:
            val = float(record[key])
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            scores[key] = val
    return scores


def _summarize_ragas(ragas_by_qid: dict[int, dict[str, float]]) -> dict[str, float]:
    scored = [s for s in ragas_by_qid.values() if s]
    if not scored:
        return {}
    keys = sorted({k for row in scored for k in row})
    return {
        key: round(sum(row[key] for row in scored if key in row) / max(1, sum(1 for row in scored if key in row)), 4)
        for key in keys
    }


def _run_ragas_batch(
    rows: list[dict],
    *,
    on_phase: Callable[[str, int, int | None], None] | None = None,
    on_scored: Callable[[int, dict[str, float]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    ragas_model_id: str | None = None,
    embedding_model_id: str | None = None,
) -> tuple[dict[str, float], list[dict], dict[int, dict[str, float]]]:
    scorable = scorable_ragas_rows(rows)
    skipped = len(rows) - len(scorable)
    if skipped:
        logger.info("RAGAS 跳过 %s 道失败/空答，仅对 %s 道成功题打分", skipped, len(scorable))
    if not scorable:
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")

    if on_phase:
        on_phase("ragas", len(scorable), 0)

    # 逐题打分：整批 evaluate 遇 429 会一张分都没有
    run_config = RunConfig(timeout=180, max_retries=2, max_wait=40, max_workers=1)
    llm = create_chat_model(**eval_chat_model_kwargs(chat_model_id=ragas_model_id))
    embeddings = create_embeddings(**eval_embedding_kwargs(embedding_model_id=embedding_model_id))
    wrapped_llm = LangchainLLMWrapper(llm, run_config=run_config)
    wrapped_emb = LangchainEmbeddingsWrapper(embeddings)

    ragas_by_qid: dict[int, dict[str, float]] = {}
    detail: list[dict] = []
    for index, row in enumerate(scorable):
        check_stop(should_stop)
        qid = int(row.get("qid", index))

        def _eval_one(payload: dict = row) -> dict:
            result = evaluate(
                EvaluationDataset.from_list([payload]),
                metrics=_METRICS,
                llm=wrapped_llm,
                embeddings=wrapped_emb,
                run_config=run_config,
            )
            records = result.to_pandas().to_dict(orient="records")
            return records[0] if records else {}

        try:
            record = call_with_tpm_retry(_eval_one)
        except EvalCancelled:
            raise
        except Exception as exc:
            logger.warning("RAGAS 跳过 qid=%s：%s", qid, exc)
            if on_phase:
                on_phase("ragas", len(scorable), index + 1)
            continue
        scores = _finite_scores(record)
        ragas_by_qid[qid] = scores
        detail.append(record)
        if on_scored and scores:
            on_scored(qid, scores)
        if on_phase:
            on_phase("ragas", len(scorable), index + 1)

    return _summarize_ragas(ragas_by_qid), detail, ragas_by_qid


def run_ragas_eval(
    *,
    n: int = 50,
    seed: int = 42,
    split: str = "validation",
    workers: int = 4,
    chat_model_id: str | None = None,
    embedding_model_id: str | None = None,
    ragas_model_id: str | None = None,
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
        chat_model_kwargs=eval_chat_model_kwargs(chat_model_id=chat_model_id),
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
            result = call_with_tpm_retry(
                lambda: agent.invoke(
                    {"messages": [{"role": "user", "content": item["question"]}]},
                    config=eval_invoke_config(kb_id, chat_model_id=chat_model_id),
                )
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

    item_by_qid = dict(indexed)
    summary, detail, ragas_by_qid = _run_ragas_batch(
        rows,
        on_phase=on_phase,
        on_scored=lambda qid, scores: on_sample
        and on_sample(_to_sample_row(qid, item_by_qid.get(qid, {}), row_by_qid.get(qid, {}), scores)),
        should_stop=should_stop,
        ragas_model_id=ragas_model_id,
        embedding_model_id=embedding_model_id,
    )

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
        chat_model_kwargs=eval_chat_model_kwargs(config),
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
            result = call_with_tpm_retry(
                lambda: agent.invoke(
                    {"messages": [{"role": "user", "content": item.question}]},
                    config=eval_invoke_config(kb_id, config),
                )
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

    item_by_qid = dict(indexed)
    summary, detail, ragas_by_qid = _run_ragas_batch(
        rows,
        on_phase=on_phase,
        on_scored=lambda qid, scores: on_sample
        and on_sample(_to_sample_row(qid, item_by_qid.get(qid, {}), row_by_qid.get(qid, {}), scores)),
        should_stop=should_stop,
        ragas_model_id=config.ragas_model_id,
        embedding_model_id=config.embedding_model_id,
    )

    samples = []
    for qid, item in indexed:
        row = row_by_qid.get(qid, {})
        samples.append(_to_sample_row(qid, item, row, ragas_by_qid.get(qid)))
        if on_sample:
            on_sample(samples[-1])

    return summary, detail, samples
