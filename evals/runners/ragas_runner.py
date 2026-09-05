"""RAGAS 评测：HotpotQA 或 JSON/Parquet 数据集 + Agent。"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from typing import Callable

from ragas import RunConfig
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
from ragas.metrics.base import MetricWithEmbeddings, MetricWithLLM

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

_SCORE_SKIP_KEYS = {
    "qid",
    "user_input",
    "response",
    "retrieved_contexts",
    "reference",
    "error",
    "latency_ms",
    "type",
}
# 单指标墙钟超时：不再走 ragas.evaluate()（Celery + nest_asyncio 易卡住，NaN 还会被我们丢掉）
_METRIC_TIMEOUT_SEC = 90
_LLM_TIMEOUT_SEC = 60
_MAX_CONTEXTS = 8
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _ragas_is_finished(_response) -> bool:
    """SiliconFlow / R1 的 finish_reason 常不是 stop，RAGAS 会当成未完成死循环重试。"""
    return True


def _strip_think(text: str) -> str:
    """去掉 Qwen3 / R1 泄漏到 content 里的思考块，避免 RAGAS JSON 解析失败。"""
    cleaned = _THINK_RE.sub("", text or "")
    return cleaned.replace("</think>", "").strip()


def _clean_llm_result(result):
    for gens in getattr(result, "generations", None) or []:
        for gen in gens:
            if getattr(gen, "text", None):
                gen.text = _strip_think(gen.text)
            message = getattr(gen, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                message.content = _strip_think(content)
    return result


class _JsonSafeLLM(LangchainLLMWrapper):
    def generate_text(self, *args, **kwargs):
        return _clean_llm_result(super().generate_text(*args, **kwargs))

    async def agenerate_text(self, *args, **kwargs):
        return _clean_llm_result(await super().agenerate_text(*args, **kwargs))


def _new_metrics():
    return [
        Faithfulness(),
        AnswerRelevancy(strictness=1),
        ContextPrecision(),
        ContextRecall(),
    ]


def _ragas_sample_payload(row: dict) -> dict:
    """RAGAS 0.2 只认这四列；qid 等额外字段不要塞进 SingleTurnSample。"""
    ctx = row.get("retrieved_contexts")
    if not isinstance(ctx, list):
        ctx = []
    return {
        "user_input": str(row.get("user_input") or ""),
        "response": str(row.get("response") or ""),
        "retrieved_contexts": [str(item) for item in ctx][:_MAX_CONTEXTS],
        "reference": str(row.get("reference") or ""),
    }


def _run_with_timeout(fn: Callable, timeout_sec: float):
    # 不能 wait=True 关线程池：evaluate 卡住时 shutdown 会跟着死等
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn)
        return future.result(timeout=timeout_sec)
    except FuturesTimeout as exc:
        raise TimeoutError(f"RAGAS 调用超过 {timeout_sec:.0f}s 仍未返回") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


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
    details = dict(row.get("details") or {})
    if "retrieved_contexts" in row:
        details["retrieved_contexts"] = list(row.get("retrieved_contexts") or [])
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
        "details": details,
    }


def samples_to_ragas_rows(samples: list[dict]) -> list[dict]:
    """把已落库的 RAG 轨迹转成 RAGAS 输入：question / contexts / answer / ground_truth。"""
    rows: list[dict] = []
    for sample in samples:
        if sample.get("error"):
            continue
        question = str(sample.get("question") or sample.get("user_input") or "").strip()
        answer = str(sample.get("response") or "").strip()
        if not question or not answer:
            continue
        details = sample.get("details") if isinstance(sample.get("details"), dict) else {}
        ctx = details.get("retrieved_contexts")
        if not isinstance(ctx, list):
            ctx = sample.get("retrieved_contexts") if isinstance(sample.get("retrieved_contexts"), list) else []
        rows.append(
            {
                "qid": int(sample.get("qid") or 0),
                "user_input": question,
                "response": answer,
                "retrieved_contexts": [str(item) for item in ctx],
                "reference": str(sample.get("reference") or ""),
            }
        )
    return rows


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
    for key, raw in record.items():
        if key in _SCORE_SKIP_KEYS:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(val):
            scores[str(key)] = val
    return scores


def _score_metric(metric, sample: SingleTurnSample, timeout_sec: float) -> float:
    """在独立事件循环里打一个指标，避开 ragas.evaluate() 的 nest_asyncio / Celery 卡死。"""

    def _in_thread() -> float:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                asyncio.wait_for(metric.single_turn_ascore(sample), timeout=timeout_sec)
            )
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            asyncio.set_event_loop(None)

    return float(_run_with_timeout(_in_thread, timeout_sec + 15))


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
    on_phase: Callable[..., None] | None = None,
    on_scored: Callable[..., None] | None = None,
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

    run_config = RunConfig(timeout=_LLM_TIMEOUT_SEC, max_retries=1, max_wait=15, max_workers=1)
    llm = create_chat_model(
        **eval_chat_model_kwargs(
            chat_model_id=ragas_model_id,
            extra={
                "extra_body": {
                    "enable_thinking": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            },
        ),
        timeout=_LLM_TIMEOUT_SEC,
        max_retries=0,
    )
    embeddings = create_embeddings(**eval_embedding_kwargs(embedding_model_id=embedding_model_id))
    wrapped_llm = _JsonSafeLLM(
        llm,
        run_config=run_config,
        is_finished_parser=_ragas_is_finished,
    )
    wrapped_emb = LangchainEmbeddingsWrapper(embeddings)

    ragas_by_qid: dict[int, dict[str, float]] = {}
    detail: list[dict] = []
    for index, row in enumerate(scorable):
        check_stop(should_stop)
        qid = int(row.get("qid", index))
        logger.info("RAGAS 开始 qid=%s（%s/%s）", qid, index + 1, len(scorable))
        if on_phase:
            on_phase("ragas", len(scorable), index)

        sample = SingleTurnSample(**_ragas_sample_payload(row))
        scores: dict[str, float] = {}
        errors: list[str] = []
        for metric in _new_metrics():
            check_stop(should_stop)
            if isinstance(metric, MetricWithLLM):
                metric.llm = wrapped_llm
            if isinstance(metric, MetricWithEmbeddings):
                metric.embeddings = wrapped_emb
            metric.init(run_config)
            try:
                raw = call_with_tpm_retry(lambda m=metric: _score_metric(m, sample, _METRIC_TIMEOUT_SEC))
                val = float(raw)
                if math.isfinite(val):
                    scores[metric.name] = val
                else:
                    errors.append(f"{metric.name}: 模型输出无法解析为分数")
            except EvalCancelled:
                raise
            except Exception as exc:
                msg = f"{metric.name}: {exc}"
                errors.append(msg)
                logger.warning("RAGAS qid=%s %s", qid, msg)
            if on_scored and scores:
                try:
                    on_scored(qid, dict(scores), "；".join(errors)[:800] if errors else None)
                except TypeError:
                    on_scored(qid, dict(scores))

        err_text = "；".join(errors)[:800] if errors else None
        ragas_by_qid[qid] = scores
        detail.append({"qid": qid, **scores, "error": err_text})
        if on_scored:
            try:
                on_scored(qid, scores, err_text)
            except TypeError:
                if scores:
                    on_scored(qid, scores)
        if not scores:
            logger.warning("RAGAS 未写出分数 qid=%s：%s", qid, err_text or "全部为 NaN")
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
    qids: list[int] | None = None,
    require_success: bool = True,
) -> tuple[dict[str, float], list[dict], list[dict]]:
    """HotpotQA + Agent + RAGAS（原 CLI 路径）。"""
    ChunkStore().init_schema()
    items = load_hotpot_sample(n=n, split=split, seed=seed)
    indexed = list(enumerate(items))
    if qids is not None:
        want = {int(q) for q in qids}
        indexed = [(qid, item) for qid, item in indexed if qid in want]
        if not indexed:
            raise ValueError("没有匹配的失败题可重试")
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

    check_stop(should_stop)
    samples = [_to_sample_row(qid, item, row_by_qid.get(qid, {})) for qid, item in indexed]
    if require_success and not any(not s.get("error") and (s.get("response") or "").strip() for s in samples):
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")
    if on_sample:
        for sample in samples:
            on_sample(sample)
    return {}, [], samples


def run_ragas_eval_dataset(
    config: EvalConfig,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    on_sample: Callable[[dict], None] | None = None,
    on_phase: Callable[[str, int, int | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_kb_created: Callable[[int, str], None] | None = None,
    qids: list[int] | None = None,
    require_success: bool = True,
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
    if qids is not None:
        want = {int(q) for q in qids}
        indexed = [(qid, item) for qid, item in indexed if qid in want]
        if not indexed:
            raise ValueError("没有匹配的失败题可重试")
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
    samples = [_to_sample_row(qid, item, row_by_qid.get(qid, {})) for qid, item in indexed]
    if require_success and not any(not s.get("error") and (s.get("response") or "").strip() for s in samples):
        raise RuntimeError("没有任何题目跑通，请检查 PG/SiliconFlow 配置")
    if on_sample:
        for sample in samples:
            on_sample(sample)
    return {}, [], samples
