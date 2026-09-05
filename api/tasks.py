"""Celery 任务：文档异步处理（上传解析、reparse、housekeeping）。

原始文件从 MinIO 拉取到临时文件供解析器读取（stored_name = storage_key）；
历史本地磁盘文档（stored_name 无 `/`）仍可从 data/uploads 回退。
"""

from __future__ import annotations

import logging

from celery import shared_task

from config.settings import settings
from services.deps import get_document_task_service

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    name="api.tasks.process_document_task",
    max_retries=2,
    default_retry_delay=5,
    acks_late=True,
)
def process_document_task(
    self,
    document_id: str,
    file_name: str,
    kb_id: int,
    storage_key: str | None = None,
    file_path: str | None = None,
    owner: str | None = None,
    process_config: dict | None = None,
) -> dict:
    """新上传文档的异步处理主入口。"""
    tasks = get_document_task_service()
    try:
        return tasks.process_upload(
            document_id=document_id,
            file_name=file_name,
            kb_id=kb_id,
            task_id=self.request.id,
            storage_key=storage_key,
            file_path=file_path,
            owner=owner,
            process_config=process_config,
        )
    except Exception as exc:
        logger.warning("document %s process failed: %s", document_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        tasks.mark_failed(document_id, owner, str(exc))
        return {"document_id": document_id, "status": "failed", "error": str(exc)}

@shared_task(
    bind=True,
    name="api.tasks.reprocess_document_task",
    max_retries=2,
    default_retry_delay=5,
    acks_late=True,
)
def reprocess_document_task(
    self,
    document_id: str,
    owner: str | None = None,
    process_config: dict | None = None,
) -> dict:
    """reparse：从 MinIO / legacy 本地还原原文件后重新解析。"""
    tasks = get_document_task_service()
    try:
        return tasks.reprocess(
            document_id=document_id,
            task_id=self.request.id,
            owner=owner,
            process_config=process_config,
        )
    except Exception as exc:
        logger.warning("document %s reparse failed: %s", document_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        tasks.mark_failed(document_id, owner, str(exc))
        return {"document_id": document_id, "status": "failed", "error": str(exc)}

@shared_task(
    bind=True,
    name="api.tasks.extract_chunk_graph_task",
    max_retries=2,
    default_retry_delay=5,
    acks_late=True,
)
def extract_chunk_graph_task(
    self,
    chunk_id: int,
    kb_id: int,
    document_id: str,
    model_id: str | None = None,
) -> dict:
    """单 chunk 实体关系抽取并写入 Neo4j。"""
    from services.graph_extract_service import GraphExtractService

    try:
        return GraphExtractService().extract_chunk(
            chunk_id=chunk_id,
            kb_id=kb_id,
            document_id=document_id,
            model_id=model_id,
        )
    except Exception as exc:
        logger.warning("graph extract chunk %s failed: %s", chunk_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {"chunk_id": chunk_id, "status": "failed", "error": str(exc)}


@shared_task(name="api.tasks.housekeeping_recover_stale")
def housekeeping_recover_stale() -> dict:
    count = get_document_task_service().recover_stale_processing(
        older_than_minutes=settings.processing_timeout_minutes,
        message=f"处理超时（>{settings.processing_timeout_minutes} 分钟），已自动终止，可点击重试",
    )
    if count:
        logger.warning("housekeeping: %s stale processing document(s) marked failed", count)
    return {"failed_stale": count}

@shared_task(name="api.tasks.cleanup_expired_temporary_attachments")
def cleanup_expired_temporary_attachments() -> dict:
    """清理过期的会话临时附件（DB + MinIO）。"""
    from utils.temporary_attachments import TemporaryAttachmentStore

    count = TemporaryAttachmentStore().cleanup_expired(batch_size=100)
    if count:
        logger.info("cleanup: removed %s expired temporary attachment(s)", count)
    return {"deleted": count}

@shared_task(
    bind=True,
    name="api.tasks.parse_temporary_attachment_task",
    max_retries=2,
    default_retry_delay=3,
    acks_late=True,
)
def parse_temporary_attachment_task(self, attachment_id: str, session_id: str) -> dict:
    """解析会话临时附件：解析器/PaddleOCR 先跑，正文仍很少才 VLM OCR。"""
    from pathlib import Path

    from ingestion.parser import ParserError, parse_document
    from services.document_task_service import run_with_materialized_path
    from utils.attachment_images import persist_attachment_parse
    from utils.attachment_vlm import maybe_vlm_enrich_attachment
    from utils.temporary_attachments import (
        STATUS_UPLOADED,
        TemporaryAttachmentStore,
        is_image_attachment,
    )

    store = TemporaryAttachmentStore()
    row = store.get(attachment_id, session_id)
    if row is None:
        return {"attachment_id": attachment_id, "status": "skipped", "reason": "not-found"}
    if row["status"] != STATUS_UPLOADED:
        return {"attachment_id": attachment_id, "status": "skipped", "reason": row["status"]}

    store.mark_processing(attachment_id)
    file_name = row["file_name"]
    storage_key = row["storage_key"]

    try:
        def _parse(path: str) -> None:
            content = ""
            image_description = ""
            image_refs: list[dict] = []
            parse_options = {
                "file_name": Path(file_name).name,
                "ocr_enabled": settings.ocr_enabled,
            }
            parsed = None
            try:
                parsed = parse_document(
                    path, engine=settings.parse_engine, parse_options=parse_options
                )
            except ParserError as exc:
                if not is_image_attachment(file_name):
                    raise
                logger.warning("图片附件文档解析失败，回退 VLM: %s", exc)

            if parsed is not None:
                content, image_refs = persist_attachment_parse(
                    session_id, attachment_id, parsed, file_name=file_name
                )

            content, image_description = maybe_vlm_enrich_attachment(
                content=content,
                file_name=file_name,
                original_storage_key=storage_key,
                image_refs=image_refs,
            )

            if not content:
                content = image_description or "（未能提取文本内容）"
            store.mark_ready(
                attachment_id,
                content=content,
                image_description=image_description,
                image_refs=image_refs,
            )

        run_with_materialized_path(storage_key, file_name, None, _parse)
    except Exception as exc:
        logger.warning("attachment %s parse failed: %s", attachment_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        store.mark_failed(attachment_id, str(exc))
        return {"attachment_id": attachment_id, "status": "failed", "error": str(exc)}

    return {"attachment_id": attachment_id, "status": "ready"}

def _eval_config_from_task(task: dict) -> "EvalConfig":
    from evals.schemas import EvalConfig

    snap = task["config_snapshot"] or {}
    suite = snap.get("suite", task["suite"])
    profile = "intent" if suite == "intent_bench" else "rag_agent"
    return EvalConfig(
        dataset_id=task["dataset_id"],
        suite=suite,
        pipeline_profile=profile,
        sample_limit=snap.get("sample_limit"),
        kb_template_id=snap.get("kb_template_id"),
        chat_model_id=snap.get("chat_model_id"),
        embedding_model_id=snap.get("embedding_model_id"),
        ragas_model_id=snap.get("ragas_model_id"),
        rerank_model_id=snap.get("rerank_model_id"),
        config_overrides=snap.get("config_overrides") or {},
        metric_layers=snap.get("metric_layers") or ["retrieval", "generation"],
        workers=snap.get("workers") or 4,
        owner=task["owner"],
    )


@shared_task(
    bind=True,
    name="api.tasks.run_evaluation_task",
    max_retries=0,
    acks_late=True,
)
def run_evaluation_task(self, task_id: str) -> dict:
    """异步执行评测任务（rag_bench 或 rag_quality）。"""
    from evals.runners.bench_runner import results_to_sample_rows, run_bench
    from evals.runners.ragas_runner import run_ragas_eval
    from utils.eval_store import EvalStore

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "missing"}
    if task["status"] == "cancelled":
        return {"task_id": task_id, "status": "cancelled"}

    config = _eval_config_from_task(task)

    store.update_task(task_id, status="running", started=True, only_if_active=True)
    task = store.get_task(task_id)
    if not task or task["status"] != "running":
        return {"task_id": task_id, "status": task["status"] if task else "missing"}

    def _should_stop() -> bool:
        return store.is_cancelled(task_id)

    def _on_kb_created(kb_id: int, _name: str) -> None:
        store.update_task(task_id, eval_kb_id=kb_id, only_if_active=True)

    try:

        def _on_sample(row: dict) -> None:
            store.upsert_sample(task_id, row)

        if config.suite == "intent_bench":
            from evals.runners.intent_runner import results_to_sample_rows as intent_rows
            from evals.runners.intent_runner import run_intent_bench

            def _prog(done: int, total: int, summary: dict) -> None:
                store.update_task(
                    task_id,
                    finished=done,
                    total=total,
                    metric_summary=summary,
                    only_if_active=True,
                )

            results, summary = run_intent_bench(
                config, on_progress=_prog, on_sample=_on_sample, should_stop=_should_stop
            )
            for row in intent_rows(results):
                store.upsert_sample(task_id, row)
            from evals.finalize import mark_result_summary

            summary = mark_result_summary(summary, partial=False, planned_total=len(results))
            store.update_task(
                task_id,
                status="success",
                metric_summary=summary,
                finished=len(results),
                total=len(results),
                finished_at=True,
                clear_err_msg=True,
            )
            return {"task_id": task_id, "status": "success", "summary": summary}

        if config.suite == "rag_quality":
            limit = config.sample_limit or 50

            def _prog(done: int, total: int) -> None:
                task_row = store.get_task(task_id) or {}
                summary = dict(task_row.get("metric_summary") or {})
                phase = summary.get("phase") or "agent"
                if phase == "ingest":
                    store.update_task(
                        task_id,
                        finished=done,
                        total=total,
                        metric_summary={
                            "phase": "ingest",
                            "ingest_finished": done,
                            "ingest_total": total,
                        },
                        only_if_active=True,
                    )
                else:
                    store.update_task(
                        task_id,
                        finished=done,
                        total=total,
                        metric_summary={
                            "phase": "agent",
                            "agent_finished": done,
                            "agent_total": total,
                            "ingest_finished": summary.get("ingest_finished"),
                            "ingest_total": summary.get("ingest_total"),
                        },
                        only_if_active=True,
                    )

            def _phase(phase: str, count: int, finished: int | None = None) -> None:
                task_row = store.get_task(task_id) or {}
                prev = dict(task_row.get("metric_summary") or {})
                if phase == "ingest":
                    store.update_task(
                        task_id,
                        finished=int(finished or 0),
                        total=count,
                        metric_summary={
                            "phase": "ingest",
                            "ingest_finished": int(finished or 0),
                            "ingest_total": count,
                        },
                        only_if_active=True,
                    )
                    return
                total = int(task_row.get("total") or count or 0)
                if phase == "agent":
                    store.update_task(
                        task_id,
                        finished=int(finished or 0),
                        total=count,
                        metric_summary={
                            "phase": "agent",
                            "agent_finished": int(finished or 0),
                            "agent_total": count,
                            "ingest_finished": prev.get("ingest_finished"),
                            "ingest_total": prev.get("ingest_total"),
                        },
                        only_if_active=True,
                    )
                    return
                if phase == "ragas":
                    store.update_task(
                        task_id,
                        finished=int(finished or 0),
                        total=count,
                        metric_summary={
                            "phase": "ragas",
                            "ragas_finished": int(finished or 0),
                            "ragas_total": count,
                            "agent_finished": prev.get("agent_finished"),
                            "agent_total": prev.get("agent_total"),
                            "ingest_finished": prev.get("ingest_finished"),
                            "ingest_total": prev.get("ingest_total"),
                        },
                        only_if_active=True,
                    )
                    return
                store.update_task(
                    task_id,
                    finished=total,
                    total=total,
                    metric_summary={
                        "phase": phase,
                        "agent_finished": total,
                        "agent_total": total,
                        "ragas_total": count,
                        "ingest_finished": prev.get("ingest_finished"),
                        "ingest_total": prev.get("ingest_total"),
                    },
                    only_if_active=True,
                )

            if config.dataset_id == "hotpot":
                _summary, _detail, samples = run_ragas_eval(
                    n=limit,
                    workers=config.workers,
                    chat_model_id=config.chat_model_id,
                    embedding_model_id=config.embedding_model_id,
                    ragas_model_id=config.ragas_model_id,
                    on_progress=_prog,
                    on_sample=_on_sample,
                    on_phase=_phase,
                    should_stop=_should_stop,
                )
            else:
                from evals.runners.ragas_runner import run_ragas_eval_dataset

                _summary, _detail, samples = run_ragas_eval_dataset(
                    config,
                    on_progress=_prog,
                    on_sample=_on_sample,
                    on_phase=_phase,
                    should_stop=_should_stop,
                    on_kb_created=_on_kb_created,
                )
            for row in samples:
                store.upsert_sample(task_id, row)
            from evals.runners.ragas_runner import samples_to_ragas_rows

            scorable = samples_to_ragas_rows(samples)
            store.update_task(
                task_id,
                status="success",
                metric_summary={
                    "phase": "collect_done",
                    "result_ready": True,
                    "partial": len(scorable) < len(samples),
                    "ready_for_ragas": True,
                    "planned_total": len(samples),
                    "sample_count": len(scorable),
                    "error_count": max(0, len(samples) - len(scorable)),
                    "agent_finished": len(samples),
                    "agent_total": len(samples),
                    "ragas_pending": len(scorable),
                },
                finished=len(samples),
                total=len(samples),
                finished_at=True,
                clear_err_msg=True,
            )
            return {"task_id": task_id, "status": "success"}

        def _prog(done: int, total: int, summary: dict) -> None:
            kb_id = summary.get("eval_kb_id")
            store.update_task(
                task_id,
                finished=done,
                total=total,
                metric_summary=summary,
                eval_kb_id=int(kb_id) if kb_id is not None else None,
                only_if_active=True,
            )

        results, summary = run_bench(
            config,
            on_progress=_prog,
            on_sample=_on_sample,
            should_stop=_should_stop,
            on_kb_created=_on_kb_created,
        )
        for row in results_to_sample_rows(results):
            store.upsert_sample(task_id, row)
        from evals.finalize import mark_result_summary

        task_row = store.get_task(task_id) or {}
        summary = mark_result_summary(
            summary,
            partial=False,
            planned_total=len(results),
            prev=task_row.get("metric_summary") if isinstance(task_row.get("metric_summary"), dict) else {},
        )
        store.update_task(
            task_id,
            status="success",
            metric_summary=summary,
            finished=len(results),
            total=len(results),
            finished_at=True,
            clear_err_msg=True,
        )
        return {"task_id": task_id, "status": "success", "summary": summary}
    except Exception as exc:
        from evals.cancel import EvalCancelled
        from evals.finalize import NoEvalSamples, finalize_task_results

        if isinstance(exc, EvalCancelled) or store.is_cancelled(task_id):
            try:
                finalized = finalize_task_results(task_id, partial=True)
                return {"task_id": task_id, "status": "success", "summary": finalized.get("metric_summary")}
            except (NoEvalSamples, FileNotFoundError):
                store.update_task(
                    task_id,
                    status="cancelled",
                    err_msg="用户中断",
                    finished_at=True,
                    only_if_active=True,
                )
                return {"task_id": task_id, "status": "cancelled"}
        logger.exception("evaluation task %s failed", task_id)
        try:
            finalized = finalize_task_results(task_id, partial=True)
            summary = dict(finalized.get("metric_summary") or {})
            summary["run_error"] = str(exc)[:800]
            if config.suite == "rag_quality":
                summary["phase"] = "collect_done"
                summary["ready_for_ragas"] = True
                summary["ragas_pending"] = int(summary.get("sample_count") or 0)
            store.update_task(task_id, metric_summary=summary, only_if_active=False)
            return {"task_id": task_id, "status": "success", "summary": summary, "error": str(exc)}
        except (NoEvalSamples, FileNotFoundError):
            pass
        store.update_task(
            task_id,
            status="failed",
            err_msg=str(exc),
            finished_at=True,
            only_if_active=True,
        )
        return {"task_id": task_id, "status": "failed", "error": str(exc)}


@shared_task(
    bind=True,
    name="api.tasks.run_ragas_score_task",
    max_retries=0,
    acks_late=True,
)
def run_ragas_score_task(self, task_id: str, ragas_model_id: str | None = None) -> dict:
    """对已收集的 RAG 轨迹做离线 RAGAS 打分。"""
    from evals.cancel import EvalCancelled
    from evals.finalize import NoEvalSamples, finalize_task_results
    from evals.runners.ragas_runner import _run_ragas_batch, samples_to_ragas_rows
    from utils.eval_store import EvalStore

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "missing"}
    if task["status"] == "cancelled":
        return {"task_id": task_id, "status": "cancelled"}

    samples = store.list_all_samples(task_id)
    rows = samples_to_ragas_rows(samples)
    if not rows:
        store.update_task(
            task_id,
            status="success",
            metric_summary={
                **(task.get("metric_summary") or {}),
                "phase": "collect_done",
                "ready_for_ragas": True,
                "ragas_error": "没有可打分的题目（需要 Agent 成功作答）",
            },
            finished_at=True,
            only_if_active=False,
        )
        return {"task_id": task_id, "status": "success"}

    prev = dict(task.get("metric_summary") or {})
    snap = dict(task.get("config_snapshot") or {})
    if ragas_model_id:
        snap["ragas_model_id"] = ragas_model_id

    store.update_task(
        task_id,
        status="running",
        config_snapshot=snap,
        metric_summary={
            **prev,
            "phase": "ragas",
            "ragas_finished": 0,
            "ragas_total": len(rows),
            "ragas_error": None,
            "ready_for_ragas": True,
        },
        total=len(rows),
        finished=0,
        started=True,
        only_if_active=False,
        clear_err_msg=True,
    )

    by_qid = {int(s["qid"]): s for s in samples}

    def _should_stop() -> bool:
        return store.is_cancelled(task_id)

    def _phase(phase: str, count: int, finished: int | None = None) -> None:
        store.update_task(
            task_id,
            finished=int(finished or 0),
            total=count,
            metric_summary={
                **prev,
                "phase": "ragas",
                "ragas_finished": int(finished or 0),
                "ragas_total": count,
                "ready_for_ragas": True,
            },
            only_if_active=True,
        )

    def _on_scored(qid: int, scores: dict, error: str | None = None) -> None:
        sample = dict(by_qid.get(qid) or {})
        metrics = dict(sample.get("metrics") or {})
        metrics["ragas"] = scores
        sample["metrics"] = metrics
        details = dict(sample.get("details") or {})
        if error:
            details["ragas_error"] = error
        else:
            details.pop("ragas_error", None)
        sample["details"] = details
        sample["qid"] = qid
        sample["question"] = sample.get("question") or ""
        store.upsert_sample(task_id, sample)
        by_qid[qid] = sample

    try:
        summary, _detail, ragas_by_qid = _run_ragas_batch(
            rows,
            on_phase=_phase,
            on_scored=_on_scored,
            should_stop=_should_stop,
            ragas_model_id=ragas_model_id,
            embedding_model_id=snap.get("embedding_model_id"),
        )
        scored = sum(1 for v in ragas_by_qid.values() if v)
        store.update_task(
            task_id,
            status="success",
            metric_summary={
                **prev,
                "phase": "done",
                "result_ready": True,
                "ready_for_ragas": True,
                "partial": scored < len(rows),
                "ragas_metrics": summary,
                "ragas_error": None if summary else "RAGAS 未写出有效分数（评分模型超时、思考链污染 JSON，或 429/TPM 限流）",
                "ragas_scored": scored,
                "ragas_total": len(rows),
                "ragas_finished": scored,
                "sample_count": len(rows),
                "error_count": max(0, len(samples) - len(rows)),
            },
            finished=len(rows),
            total=len(rows),
            finished_at=True,
            only_if_active=False,
            clear_err_msg=True,
        )
        return {"task_id": task_id, "status": "success", "ragas_scored": scored}
    except Exception as exc:
        if isinstance(exc, EvalCancelled) or store.is_cancelled(task_id):
            try:
                finalized = finalize_task_results(task_id, partial=True)
                summary = dict(finalized.get("metric_summary") or {})
                summary["ready_for_ragas"] = True
                summary["phase"] = "done" if summary.get("ragas_metrics") else "collect_done"
                store.update_task(task_id, metric_summary=summary, only_if_active=False)
            except (NoEvalSamples, FileNotFoundError):
                store.update_task(task_id, status="cancelled", err_msg="用户中断", finished_at=True)
            return {"task_id": task_id, "status": "cancelled"}
        logger.exception("ragas score task %s failed", task_id)
        store.update_task(
            task_id,
            status="success",
            metric_summary={
                **prev,
                "phase": "collect_done",
                "ready_for_ragas": True,
                "ragas_error": str(exc)[:800],
            },
            finished_at=True,
            only_if_active=False,
        )
        return {"task_id": task_id, "status": "success", "error": str(exc)}


def _summarize_after_retry(store, task_id: str, task: dict, prev: dict) -> dict:
    from evals.failed import retryable_qids
    from evals.finalize import mark_result_summary, summarize_sample_rows
    from evals.runners.ragas_runner import samples_to_ragas_rows

    samples = store.list_all_samples(task_id)
    suite = task.get("suite") or ""
    leftover = retryable_qids(samples, suite=suite)
    if suite == "rag_quality":
        scorable = samples_to_ragas_rows(samples)
        summary = {
            **prev,
            "phase": "done" if prev.get("ragas_metrics") else "collect_done",
            "result_ready": True,
            "partial": bool(leftover),
            "ready_for_ragas": True,
            "planned_total": len(samples),
            "sample_count": len(scorable),
            "error_count": len(leftover),
            "agent_finished": len(samples),
            "agent_total": len(samples),
            "ragas_pending": len(scorable),
        }
        if leftover:
            summary["phase"] = "collect_done" if not prev.get("ragas_metrics") else summary["phase"]
        store.update_task(
            task_id,
            status="success",
            metric_summary=summary,
            finished=len(samples),
            total=len(samples),
            finished_at=True,
            only_if_active=False,
            clear_err_msg=True,
        )
        return summary

    summary = mark_result_summary(
        summarize_sample_rows(samples),
        partial=bool(leftover),
        planned_total=len(samples),
        prev=prev,
    )
    summary["error_count"] = len(leftover)
    store.update_task(
        task_id,
        status="success",
        metric_summary=summary,
        finished=len(samples),
        total=max(int(task.get("total") or 0), len(samples)),
        finished_at=True,
        only_if_active=False,
        clear_err_msg=True,
    )
    return summary


@shared_task(
    bind=True,
    name="api.tasks.run_eval_retry_task",
    max_retries=0,
    acks_late=True,
)
def run_eval_retry_task(self, task_id: str, qids: list[int] | None = None) -> dict:
    """只重跑失败题（运行出错，或 rag_quality 空答），写回原任务。"""
    from evals.cancel import EvalCancelled
    from evals.failed import retryable_qids
    from evals.finalize import NoEvalSamples, finalize_task_results
    from utils.eval_store import EvalStore

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "missing"}

    samples = store.list_all_samples(task_id)
    failed = retryable_qids(samples, suite=task["suite"])
    want = [int(q) for q in (qids or failed)]
    target = [q for q in want if q in set(failed)]
    if not target:
        return {"task_id": task_id, "status": "success", "retried": 0}

    prev = dict(task.get("metric_summary") or {})
    config = _eval_config_from_task(task)
    store.update_task(
        task_id,
        status="running",
        metric_summary={
            **prev,
            "phase": "agent" if config.suite == "rag_quality" else "eval",
            "retry_total": len(target),
            "retry_finished": 0,
        },
        total=len(target),
        finished=0,
        started=True,
        only_if_active=False,
        clear_err_msg=True,
    )

    def _should_stop() -> bool:
        return store.is_cancelled(task_id)

    def _on_sample(row: dict) -> None:
        store.upsert_sample(task_id, row)

    def _on_kb_created(kb_id: int, _name: str) -> None:
        store.update_task(task_id, eval_kb_id=kb_id, only_if_active=True)

    retry_n = len(target)

    def _prog_simple(done: int, total: int, summary: dict | None = None) -> None:
        payload = dict(prev)
        if isinstance(summary, dict):
            payload.update(summary)
        phase = str(payload.get("phase") or ("agent" if config.suite == "rag_quality" else "eval"))
        payload["phase"] = phase
        payload["retry_total"] = retry_n
        if phase == "ingest":
            if "ingest_finished" not in (summary or {}):
                payload["ingest_finished"] = int(done)
                payload["ingest_total"] = int(total)
            payload["retry_finished"] = 0
            store.update_task(
                task_id,
                finished=0,
                total=retry_n,
                metric_summary=payload,
                only_if_active=True,
            )
            return
        payload["retry_finished"] = int(done)
        store.update_task(
            task_id,
            finished=int(done),
            total=retry_n,
            metric_summary=payload,
            only_if_active=True,
        )

    def _prog_ragas(done: int, total: int) -> None:
        _prog_simple(done, retry_n, {"phase": "agent"})

    def _phase(phase: str, count: int, finished: int | None = None) -> None:
        extra = {"phase": phase}
        if phase == "ingest":
            extra["ingest_finished"] = int(finished or 0)
            extra["ingest_total"] = int(count)
            _prog_simple(0, retry_n, extra)
            return
        _prog_simple(int(finished or 0), retry_n, extra)

    try:
        if config.suite == "intent_bench":
            from evals.runners.intent_runner import run_intent_bench

            run_intent_bench(
                config,
                on_progress=lambda done, total, summary: _prog_simple(done, total, summary),
                on_sample=_on_sample,
                should_stop=_should_stop,
                qids=target,
            )
        elif config.suite == "rag_quality":
            limit = config.sample_limit or 50
            if config.dataset_id == "hotpot":
                from evals.runners.ragas_runner import run_ragas_eval

                run_ragas_eval(
                    n=limit,
                    workers=config.workers,
                    chat_model_id=config.chat_model_id,
                    embedding_model_id=config.embedding_model_id,
                    on_progress=_prog_ragas,
                    on_sample=_on_sample,
                    on_phase=_phase,
                    should_stop=_should_stop,
                    qids=target,
                    require_success=False,
                )
            else:
                from evals.runners.ragas_runner import run_ragas_eval_dataset

                run_ragas_eval_dataset(
                    config,
                    on_progress=_prog_ragas,
                    on_sample=_on_sample,
                    on_phase=_phase,
                    should_stop=_should_stop,
                    on_kb_created=_on_kb_created,
                    qids=target,
                    require_success=False,
                )
        else:
            from evals.runners.bench_runner import run_bench

            run_bench(
                config,
                on_progress=lambda done, total, summary: _prog_simple(done, total, summary),
                on_sample=_on_sample,
                should_stop=_should_stop,
                on_kb_created=_on_kb_created,
                qids=target,
            )

        summary = _summarize_after_retry(store, task_id, task, prev)
        leftover = int((summary or {}).get("error_count") or 0)
        return {"task_id": task_id, "status": "success", "retried": len(target), "error_count": leftover}
    except Exception as exc:
        if isinstance(exc, EvalCancelled) or store.is_cancelled(task_id):
            try:
                finalize_task_results(task_id, partial=True)
            except (NoEvalSamples, FileNotFoundError):
                store.update_task(task_id, status="cancelled", err_msg="用户中断", finished_at=True)
            return {"task_id": task_id, "status": "cancelled"}
        logger.exception("retry failed samples %s failed", task_id)
        try:
            _summarize_after_retry(store, task_id, task, {**prev, "retry_error": str(exc)[:800]})
        except Exception:
            store.update_task(
                task_id,
                status="success",
                metric_summary={**prev, "retry_error": str(exc)[:800]},
                finished_at=True,
                only_if_active=False,
            )
        return {"task_id": task_id, "status": "success", "error": str(exc)}

