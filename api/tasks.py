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
    from evals.schemas import EvalConfig
    from utils.eval_store import EvalStore

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        return {"task_id": task_id, "status": "missing"}

    snap = task["config_snapshot"]
    config = EvalConfig(
        dataset_id=task["dataset_id"],
        suite=snap.get("suite", task["suite"]),
        pipeline_profile=snap.get("pipeline_profile", task["pipeline_profile"]),
        sample_limit=snap.get("sample_limit"),
        kb_template_id=snap.get("kb_template_id"),
        chat_model_id=snap.get("chat_model_id"),
        rerank_model_id=snap.get("rerank_model_id"),
        config_overrides=snap.get("config_overrides") or {},
        metric_layers=snap.get("metric_layers") or ["retrieval", "generation"],
        workers=snap.get("workers") or 4,
        owner=task["owner"],
    )

    store.update_task(task_id, status="running", started=True)
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
                )

            results, summary = run_intent_bench(config, on_progress=_prog, on_sample=_on_sample)
            for row in intent_rows(results):
                store.upsert_sample(task_id, row)
            store.update_task(
                task_id,
                status="success",
                metric_summary=summary,
                finished=len(results),
                total=len(results),
                finished_at=True,
            )
            return {"task_id": task_id, "status": "success", "summary": summary}

        if config.suite == "rag_quality":
            limit = config.sample_limit or 50

            def _prog(done: int, total: int) -> None:
                store.update_task(
                    task_id,
                    finished=done,
                    total=total,
                    metric_summary={
                        "phase": "agent",
                        "agent_finished": done,
                        "agent_total": total,
                    },
                )

            def _phase(phase: str, count: int) -> None:
                task_row = store.get_task(task_id) or {}
                total = int(task_row.get("total") or count or 0)
                store.update_task(
                    task_id,
                    finished=total,
                    total=total,
                    metric_summary={
                        "phase": phase,
                        "agent_finished": total,
                        "agent_total": total,
                        "ragas_total": count,
                    },
                )

            if config.dataset_id == "hotpot":
                summary, _detail, samples = run_ragas_eval(
                    n=limit,
                    workers=config.workers,
                    on_progress=_prog,
                    on_sample=_on_sample,
                    on_phase=_phase,
                )
            else:
                from evals.runners.ragas_runner import run_ragas_eval_dataset

                summary, _detail, samples = run_ragas_eval_dataset(
                    config,
                    on_progress=_prog,
                    on_sample=_on_sample,
                    on_phase=_phase,
                )
            for row in samples:
                store.upsert_sample(task_id, row)
            store.update_task(
                task_id,
                status="success",
                metric_summary={
                    "phase": "done",
                    "ragas_metrics": summary,
                    "sample_count": len(samples),
                    "agent_finished": len(samples),
                    "agent_total": len(samples),
                },
                finished=len(samples),
                total=len(samples),
                finished_at=True,
            )
            return {"task_id": task_id, "status": "success"}

        def _prog(done: int, total: int, summary: dict) -> None:
            store.update_task(
                task_id,
                finished=done,
                total=total,
                metric_summary=summary,
            )

        results, summary = run_bench(config, on_progress=_prog, on_sample=_on_sample)
        for row in results_to_sample_rows(results):
            store.upsert_sample(task_id, row)
        store.update_task(
            task_id,
            status="success",
            metric_summary=summary,
            finished=len(results),
            total=len(results),
            finished_at=True,
        )
        return {"task_id": task_id, "status": "success", "summary": summary}
    except Exception as exc:
        logger.exception("evaluation task %s failed", task_id)
        store.update_task(task_id, status="failed", err_msg=str(exc), finished_at=True)
        return {"task_id": task_id, "status": "failed", "error": str(exc)}
