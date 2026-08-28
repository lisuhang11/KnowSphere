"""Celery 文档任务编排：状态机 + materialize + 摄取/reparse 委托。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.ingestion_service import IngestionService
from stores.facade import ChunkStore
from utils.object_store import materialize_document_path

logger = logging.getLogger(__name__)

def run_with_materialized_path(
    stored_name: str | None,
    file_name: str,
    legacy_file_path: str | None,
    worker: Callable[[str], dict],
) -> dict:
    path, is_temp = materialize_document_path(stored_name, file_name, legacy_file_path)
    try:
        return worker(path)
    finally:
        if is_temp:
            Path(path).unlink(missing_ok=True)

class DocumentTaskService:
    def __init__(
        self,
        store: ChunkStore() | None = None,
        ingestion: IngestionService | None = None,
    ) -> None:
        self.store = store or ChunkStore()
        self.ingestion = ingestion or IngestionService(self.store)

    def process_upload(
        self,
        *,
        document_id: str,
        file_name: str,
        kb_id: int,
        task_id: str,
        storage_key: str | None = None,
        file_path: str | None = None,
        owner: str | None = None,
        process_config: dict | None = None,
    ) -> dict:
        row = self.store.get_document_status_row(document_id, owner)
        if row is None:
            logger.info("skip %s: row deleted before process", document_id)
            return {"document_id": document_id, "status": "skipped", "reason": "deleted"}
        if row["status"] == self.store.STATUS_CANCELLED:
            logger.info("skip %s: cancelled before process", document_id)
            return {"document_id": document_id, "status": "skipped", "reason": "cancelled"}

        key = storage_key or row.get("stored_name")
        if not self.store.mark_document_processing(
            document_id, owner=owner, task_id=task_id, stage="parsing"
        ):
            logger.info("skip %s: not in pending on start", document_id)
            return {"document_id": document_id, "status": "skipped"}

        result = run_with_materialized_path(
            key,
            file_name,
            file_path,
            lambda path: self.ingestion.ingest_file(
                path,
                owner=owner,
                kb_id=kb_id,
                process_config=process_config,
                document_id=document_id,
                file_name=file_name,
            ),
        )
        return self._finalize(document_id, owner, result)

    def reprocess(
        self,
        *,
        document_id: str,
        task_id: str,
        owner: str | None = None,
        process_config: dict | None = None,
    ) -> dict:
        row = self.store.get_document_status_row(document_id, owner)
        if row is None:
            return {"document_id": document_id, "status": "skipped", "reason": "deleted"}
        if row["status"] == self.store.STATUS_PROCESSING:
            return {"document_id": document_id, "status": "skipped", "reason": "already-processing"}

        try:
            materialize_document_path(row["stored_name"], row["file_name"])
        except FileNotFoundError as exc:
            self.store.mark_document_failed(
                document_id,
                owner=owner,
                error_message="原始文件已不在对象存储，无法重新解析",
            )
            return {"document_id": document_id, "status": "failed", "error": str(exc)}

        if not self.store.mark_document_processing(
            document_id,
            owner=owner,
            task_id=task_id,
            stage="reparsing",
            from_statuses=(
                self.store.STATUS_PENDING,
                self.store.STATUS_FAILED,
                self.store.STATUS_CANCELLED,
                self.store.STATUS_COMPLETED,
            ),
        ):
            return {"document_id": document_id, "status": "skipped"}

        result = run_with_materialized_path(
            row["stored_name"],
            row["file_name"],
            None,
            lambda path: self.ingestion.reparse_document(
                path,
                document_id=document_id,
                owner=owner,
                kb_id=row["knowledge_base_id"],
                process_config=process_config,
                file_name=row["file_name"],
            ),
        )
        return self._finalize(document_id, owner, result)

    def mark_failed(self, document_id: str, owner: str | None, error_message: str) -> None:
        self.store.mark_document_failed(document_id, owner=owner, error_message=error_message)

    def recover_stale_processing(self, older_than_minutes: int, message: str) -> int:
        return self.store.fail_stale_processing(
            older_than_minutes=older_than_minutes,
            message=message,
        )

    def _finalize(self, document_id: str, owner: str | None, result: dict[str, Any]) -> dict:
        row = self.store.get_document_status_row(document_id, owner)
        if row is None:
            logger.info("skip %s: row deleted during process, cleaning chunks", document_id)
            self.store.delete_document(document_id, owner)
            return {"document_id": document_id, "status": "skipped", "reason": "deleted-during-process"}
        if row["status"] == self.store.STATUS_CANCELLED:
            logger.info("keep %s: cancelled during process, data retained", document_id)
            return {"document_id": document_id, "status": "cancelled"}

        self.store.mark_document_completed(document_id, owner)
        logger.info("document %s completed (%s chunks)", document_id, result.get("chunk_count"))
        return {"document_id": document_id, "status": "completed", **result}
