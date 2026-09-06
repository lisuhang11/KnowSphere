"""文档业务编排：上传落库、删除、取消、reparse 校验。"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from config.settings import settings
from ingestion.parser import ALLOWED_EXTENSIONS
from services.errors import BadRequestError, NotFoundError, UnavailableError
from stores.facade import ChunkStore
from utils.model_credentials import ensure_embedding_model_ready
from utils.object_store import (
    ObjectStoreError,
    build_document_storage_key,
    collect_document_storage_keys,
    get_object_store,
    guess_content_type,
    materialize_document_path,
    require_object_store,
)

logger = logging.getLogger(__name__)

class DocumentService:
    def __init__(self, store: ChunkStore() | None = None) -> None:
        self.store = store or ChunkStore()

    def validate_extension(self, file_name: str) -> str:
        ext = Path(file_name or "").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise BadRequestError(f"仅支持 {sorted(ALLOWED_EXTENSIONS)} 格式")
        return ext

    def get_kb_for_upload(self, kb_id: int, owner: str | None = None) -> dict[str, Any]:
        owner_val = owner or settings.default_owner
        kb = self.store.get_knowledge_base(kb_id, owner=owner_val)
        if kb is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        try:
            ensure_embedding_model_ready(kb["embedding_model_id"])
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        return kb

    def begin_upload(
        self,
        *,
        kb_id: int,
        file_name: str,
        file_bytes: bytes,
        owner: str | None = None,
        process_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """写入 MinIO + documents(pending)。失败时回滚对象存储。"""
        self.validate_extension(file_name)
        kb = self.get_kb_for_upload(kb_id, owner=owner)
        from utils.audio import ensure_kb_can_accept_audio, is_audio_filename

        if is_audio_filename(file_name):
            try:
                ensure_kb_can_accept_audio(kb)
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc

        try:
            object_store = require_object_store()
        except ObjectStoreError as exc:
            raise UnavailableError(str(exc)) from exc

        owner_val = owner or settings.default_owner
        clean_name = Path(file_name or "").name or "upload"
        document_id = uuid.uuid4().hex[:12]
        storage_key = build_document_storage_key(owner_val, kb_id, document_id, clean_name)

        try:
            object_store.put_bytes(file_bytes, storage_key, guess_content_type(clean_name))
        except ObjectStoreError as exc:
            raise UnavailableError(f"上传 MinIO 失败: {exc}") from exc

        try:
            self.store.insert_document_pending(
                document_id,
                clean_name,
                storage_key,
                kb_id,
                owner=owner,
                process_config=process_config,
            )
        except Exception as exc:
            object_store.delete_object(storage_key)
            raise UnavailableError(f"写入文档记录失败: {exc}") from exc

        return {
            "document_id": document_id,
            "file_name": clean_name,
            "kb_id": kb_id,
            "storage_key": storage_key,
            "status": self.store.STATUS_PENDING,
        }

    def mark_enqueue_failed(self, document_id: str, owner: str | None, error: str) -> None:
        self.store.mark_document_failed(document_id, owner=owner, error_message=error)

    def list_documents(self, kb_id: int, owner: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_documents(kb_id, owner=owner)

    def get_status(self, document_id: str, owner: str | None = None) -> dict[str, Any]:
        row = self.store.get_document_status_row(document_id, owner=owner)
        if row is None:
            raise NotFoundError(f"文档不存在: {document_id}")
        return {
            "document_id": row["document_id"],
            "status": row["status"],
            "error_message": row["error_message"],
            "stage": row["stage"],
            "updated_at": row["updated_at"],
        }

    def get_meta(self, document_id: str, owner: str | None = None) -> dict[str, Any]:
        meta = self.store.get_document_meta(document_id, owner=owner)
        if meta is None:
            raise NotFoundError(f"文档不存在: {document_id}")
        return meta

    def cancel(self, document_id: str, owner: str | None = None) -> dict[str, Any]:
        row = self.store.get_document_status_row(document_id, owner=owner)
        if row is None:
            raise NotFoundError(f"文档不存在: {document_id}")
        if row["status"] not in (self.store.STATUS_PENDING, self.store.STATUS_PROCESSING):
            return {"document_id": document_id, "status": row["status"]}
        ok = self.store.mark_document_cancelled(document_id, owner=owner)
        return {
            "document_id": document_id,
            "status": self.store.STATUS_CANCELLED if ok else row["status"],
        }

    def delete(
        self,
        document_id: str,
        owner: str | None = None,
    ) -> dict[str, Any]:
        """删除 DB 记录，返回待清理的对象存储 key 与 Celery task_id。"""
        meta = self.store.get_document_meta(document_id, owner=owner)
        row = self.store.get_document_status_row(document_id, owner=owner)
        storage_keys = collect_document_storage_keys(
            (meta or {}).get("stored_name"),
            (meta or {}).get("image_refs"),
        )
        deleted = self.store.delete_document(document_id, owner=owner)
        if row is None and deleted == 0:
            raise NotFoundError(f"文档不存在: {document_id}")
        try:
            from services.graph_extract_service import GraphExtractService

            kb_id = (row or {}).get("knowledge_base_id") or (meta or {}).get("knowledge_base_id")
            if kb_id is not None:
                GraphExtractService(self.store).delete_document_graph(int(kb_id), document_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete document graph failed for %s: %s", document_id, exc)
        return {
            "document_id": document_id,
            "deleted_chunks": deleted,
            "storage_keys": storage_keys,
            "task_id": row.get("task_id") if row else None,
        }

    def cleanup_object_storage(self, storage_keys: list[str]) -> None:
        obj_store = get_object_store()
        if obj_store is not None and storage_keys:
            obj_store.delete_objects(storage_keys)

    def prepare_reparse(self, document_id: str) -> dict[str, Any]:
        """校验 reparse 前置条件，确保原文件可 materialize。"""
        row = self.store.get_document_status_row(document_id)
        if row is None:
            raise NotFoundError(f"文档不存在: {document_id}")
        if row["status"] == self.store.STATUS_PROCESSING:
            raise ConflictError("文档正在处理中，请等待完成后再重新解析")
        if row["knowledge_base_id"] is None:
            raise BadRequestError("文档未归属知识库，无法重新解析")
        if self.store.get_knowledge_base(row["knowledge_base_id"]) is None:
            raise NotFoundError(f"知识库不存在: {row['knowledge_base_id']}")

        try:
            materialize_document_path(row["stored_name"], row["file_name"])
        except FileNotFoundError as exc:
            raise NotFoundError(str(exc)) from exc

        existing = self.store.get_document_config(document_id) or {}
        return {
            "row": row,
            "process_config": existing.get("process_config"),
        }

    def list_chunks(
        self,
        document_id: str,
        owner: str | None = None,
        page: int = 1,
        page_size: int = 20,
        include_parent_text: bool = False,
        offset: int | None = None,
    ) -> dict[str, Any]:
        return self.store.list_chunks(
            document_id,
            owner=owner,
            page=page,
            page_size=page_size,
            include_parent_text=include_parent_text,
            offset=offset,
        )

    def get_chunk(self, chunk_id: int, owner: str | None = None) -> dict[str, Any]:
        rows = self.store.get_chunks_by_ids([chunk_id], owner=owner)
        if not rows:
            raise NotFoundError(f"分块不存在: {chunk_id}")
        return rows[0]
