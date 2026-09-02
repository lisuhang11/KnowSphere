"""文档表 CRUD 与解析状态机"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from config.settings import get_current_owner, settings
from models.dimensions import MAX_HNSW_DIM
from stores.common import (
    KB_COLS,
    KB_COL_COUNT,
    RETRIEVABLE_CHUNK_WHERE,
    embedding_column,
    kb_cols_prefixed,
    kb_row_to_dict,
    load_jsonb,
)
from stores.rrf import rrf_fuse
from utils.tokens import estimate_tokens

class DocumentRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    def upsert_document(
        self,
        document_id: str,
        file_name: str,
        kb_id: int | None,
        owner: str | None = None,
        process_config: dict[str, Any] | None = None,
        applied_strategy: str | None = None,
        image_refs: list[dict[str, Any]] | None = None,
    ) -> None:
        """写入/更新文档级处理配置行（documents 表）。

        process_config 只存用户显式指定的字段（omitempty 语义），None/空 =
        全部跟随知识库默认。applied_strategy 为本次实际生效的切分 tier。
        image_refs：解析出的内嵌图片元数据（含 MinIO storage_key）；None =
        不修改（reparse 未提供时保留旧值），显式传 [] 才会清空。
        """
        owner = owner or get_current_owner() or settings.default_owner
        image_refs_json = Jsonb(image_refs) if image_refs is not None else None
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (document_id, file_name, knowledge_base_id, owner, process_config,
                     applied_strategy, image_refs, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed')
                ON CONFLICT (document_id) DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    knowledge_base_id = EXCLUDED.knowledge_base_id,
                    process_config = COALESCE(EXCLUDED.process_config, documents.process_config),
                    applied_strategy = COALESCE(EXCLUDED.applied_strategy, documents.applied_strategy),
                    image_refs = COALESCE(EXCLUDED.image_refs, documents.image_refs),
                    updated_at = now()
                """,
                (
                    document_id,
                    file_name,
                    kb_id,
                    owner,
                    Jsonb(process_config or {}),
                    applied_strategy,
                    image_refs_json,
                ),
            )
            conn.commit()

    def get_document_config(
        self, document_id: str, owner: str | None = None
    ) -> dict[str, Any] | None:
        """取文档级处理配置（documents 行）。无该行（存量/未显式配置）返回 None。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT file_name, knowledge_base_id, process_config, applied_strategy
                FROM documents
                WHERE document_id = %s AND owner = %s
                """,
                (document_id, owner),
            ).fetchone()
        if not row:
            return None
        return {
            "file_name": row[0],
            "kb_id": row[1],
            "process_config": load_jsonb(row[2]),
            "applied_strategy": row[3],
        }

    def get_document_meta(self, document_id: str, owner: str | None = None) -> dict[str, Any] | None:
        """取单个文档的聚合元数据（file_name / chunk_count / updated_at / 生效策略 / 解析状态）。

        以 documents 为主表（LEFT JOIN chunks）：pending/processing/failed 等
        尚未或永远没有分块的文档也能查到，供前端状态展示。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT doc.document_id, doc.file_name, doc.status, doc.error_message,
                       doc.stage, doc.stored_name, doc.process_config, doc.applied_strategy,
                       doc.image_refs, doc.knowledge_base_id, doc.updated_at,
                       COUNT(ch.id) AS chunk_count
                FROM documents doc
                LEFT JOIN chunks ch
                    ON ch.document_id = doc.document_id AND ch.owner = doc.owner
                WHERE doc.document_id = %s AND doc.owner = %s
                GROUP BY doc.document_id
                """,
                (document_id, owner),
            ).fetchone()
        if not row:
            return None
        return {
            "document_id": row[0],
            "file_name": row[1],
            "status": row[2],
            "error_message": row[3],
            "stage": row[4],
            "stored_name": row[5],
            "process_config": load_jsonb(row[6]) or {},
            "applied_strategy": row[7],
            "image_refs": load_jsonb(row[8]) or [],
            "knowledge_base_id": row[9],
            "updated_at": row[10].isoformat() if row[10] else None,
            "chunk_count": row[11],
        }

    def list_documents(self, kb_id: int, owner: str | None = None) -> list[dict[str, Any]]:
        """列出某知识库内的文档：document_id、file_name、分块数、最近写入时间、生效策略、解析状态。

        以 documents 为主表：pending/processing/failed/cancelled 等尚未或永远
        没有分块的文档也出现在列表中（ 列表同样展示未完成文档）。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT doc.document_id, doc.file_name, doc.status, doc.error_message,
                       doc.stage, doc.updated_at,
                       doc.process_config, doc.applied_strategy, doc.image_refs,
                       COUNT(ch.id) AS chunk_count
                FROM documents doc
                LEFT JOIN chunks ch
                    ON ch.document_id = doc.document_id AND ch.owner = doc.owner
                WHERE doc.knowledge_base_id = %s AND doc.owner = %s
                GROUP BY doc.document_id
                ORDER BY doc.updated_at DESC
                """,
                (kb_id, owner),
            ).fetchall()
        return [
            {
                "document_id": r[0],
                "file_name": r[1],
                "status": r[2],
                "error_message": r[3],
                "stage": r[4],
                "updated_at": r[5].isoformat() if r[5] else None,
                "process_config": load_jsonb(r[6]) or {},
                "applied_strategy": r[7],
                "image_refs": load_jsonb(r[8]) or [],
                "chunk_count": r[9],
            }
            for r in rows
        ]

    def delete_document(self, document_id: str, owner: str | None = None) -> int:
        """删除某文档的全部分块（同一事务内一并清理 documents 行），返回删除条数。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute(
                "DELETE FROM chunks WHERE document_id = %s AND owner = %s",
                (document_id, owner),
            )
            conn.execute(
                "DELETE FROM documents WHERE document_id = %s AND owner = %s",
                (document_id, owner),
            )
            conn.commit()
            return cur.rowcount

    def get_document_status_row(
        self, document_id: str, owner: str | None = None
    ) -> dict[str, Any] | None:
        """取文档状态行（status/error_message/stage/task_id/stored_name 等）。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT document_id, file_name, knowledge_base_id, status, "
                "error_message, stage, task_id, stored_name, updated_at "
                "FROM documents WHERE document_id = %s AND owner = %s",
                (document_id, owner),
            ).fetchone()
        if not row:
            return None
        return {
            "document_id": row[0],
            "file_name": row[1],
            "knowledge_base_id": row[2],
            "status": row[3],
            "error_message": row[4],
            "stage": row[5],
            "task_id": row[6],
            "stored_name": row[7],
            "updated_at": row[8].isoformat() if row[8] else None,
        }

    def transition_document_status(
        self,
        document_id: str,
        to_status: str,
        from_statuses: tuple[str, ...],
        owner: str | None = None,
        error_message: str | None = None,
        stage: str | None = None,
        task_id: str | None = None,
    ) -> bool:
        """带状态守卫的原子状态转换；未命中（行不存在/不在 from_statuses）返回 False。"""
        owner = owner or get_current_owner() or settings.default_owner
        sets = ["status = %s", "updated_at = now()"]
        params: list[Any] = [to_status]
        if error_message is not None:
            sets.append("error_message = %s")
            params.append(error_message)
        elif to_status in (self.STATUS_PROCESSING, self.STATUS_COMPLETED):
            # 进入 processing / 完成时清空历史错误，避免残留上一轮失败信息
            sets.append("error_message = NULL")
        if stage is not None:
            sets.append("stage = %s")
            params.append(stage)
        elif to_status in (self.STATUS_PROCESSING, self.STATUS_COMPLETED):
            # 进入 processing 但未指定阶段 / 完成时清空 stage，
            # 避免残留上一轮 parsing/chunking/embedding 徽标
            sets.append("stage = NULL")
        if task_id is not None:
            sets.append("task_id = %s")
            params.append(task_id)
        params.extend([document_id, owner])
        # psycopg3 不支持 "IN %s"（列表会转成数组字面量），改用动态占位符
        placeholders = ", ".join(["%s"] * len(from_statuses))
        params.extend(list(from_statuses))
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute(
                f"UPDATE documents SET {', '.join(sets)} "
                "WHERE document_id = %s AND owner = %s "
                f"AND status IN ({placeholders})",
                params,
            )
            conn.commit()
            return cur.rowcount > 0

    def insert_document_pending(
        self,
        document_id: str,
        file_name: str,
        stored_name: str,
        kb_id: int,
        owner: str | None = None,
        process_config: dict[str, Any] | None = None,
    ) -> None:
        """上传入口：先落 documents 行（pending），再投递 Celery 任务。

        与  CreateKnowledgeFromFile 一致——文件与行先落库，状态从
        pending 开始演进，前端立即拿到 document_id 轮询。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO documents
                    (document_id, file_name, knowledge_base_id, owner, process_config,
                     status, stored_name)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                ON CONFLICT (document_id) DO UPDATE SET
                    file_name = EXCLUDED.file_name,
                    stored_name = EXCLUDED.stored_name,
                    knowledge_base_id = EXCLUDED.knowledge_base_id,
                    status = 'pending',
                    error_message = NULL,
                    updated_at = now()
                """,
                (
                    document_id,
                    file_name,
                    kb_id,
                    owner,
                    Jsonb(process_config or {}),
                    stored_name,
                ),
            )
            conn.commit()

    def mark_document_processing(
        self,
        document_id: str,
        owner: str | None = None,
        task_id: str | None = None,
        stage: str = "parsing",
        from_statuses: tuple[str, ...] | None = None,
    ) -> bool:
        """pending/failed/cancelled/completed → processing（任务启动或 reparse）。"""
        if from_statuses is None:
            from_statuses = (self.STATUS_PENDING, self.STATUS_FAILED,
                             self.STATUS_CANCELLED, self.STATUS_COMPLETED)
        return self.transition_document_status(
            document_id, self.STATUS_PROCESSING, from_statuses,
            owner=owner, stage=stage, task_id=task_id,
        )

    def update_stage(self, document_id: str, stage: str, owner: str | None = None) -> bool:
        """processing 中更新阶段名（parsing → chunking → embedding），并刷新 updated_at 心跳。

        仅命中 processing 行（CLI/测试直调时无 documents 行则为 no-op），
        与 mark_document_* 的"带守卫 UPDATE"一致；stage 细分对齐 
        docreader → chunking → embedding 的处理阶段展示。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute(
                "UPDATE documents SET stage = %s, updated_at = now() "
                "WHERE document_id = %s AND owner = %s AND status = %s",
                (stage, document_id, owner, self.STATUS_PROCESSING),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_document_completed(self, document_id: str, owner: str | None = None) -> bool:
        """processing → completed；取消/删除的行不会被 promote（守卫）。"""
        return self.transition_document_status(
            document_id, self.STATUS_COMPLETED, (self.STATUS_PROCESSING,),
            owner=owner, stage=None,
        )

    def mark_document_failed(
        self, document_id: str, owner: str | None = None, error_message: str | None = None
    ) -> bool:
        """processing → failed；cancelled 的行不会被覆盖。"""
        return self.transition_document_status(
            document_id, self.STATUS_FAILED, (self.STATUS_PROCESSING,),
            owner=owner, error_message=error_message or "处理失败",
        )

    def mark_document_cancelled(self, document_id: str, owner: str | None = None) -> bool:
        """pending/processing → cancelled（用户取消解析，保留已写数据，可 reparse）。"""
        return self.transition_document_status(
            document_id, self.STATUS_CANCELLED,
            (self.STATUS_PENDING, self.STATUS_PROCESSING),
            owner=owner, error_message=None,
        )

    def fail_stale_processing(
        self,
        owner: str | None = None,
        older_than_minutes: int = 30,
        message: str = "处理超时，已自动终止，可点击重试",
    ) -> int:
        """housekeeping 兜底：处理中超时（updated_at 早于阈值）的文档置 failed。

        参考  housekeeping 的孤儿回收：Celery 的 acks_late + 重投已覆盖
        worker 崩溃导致的任务丢失，这里兜底的是"任务进程被 SIGKILL/异常退出且
        Celery 未能重投"或"任务永远等不到"的极端情况。置 failed 而非重新入队，
        避免反复空转；用户可手动 reparse。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute(
                "UPDATE documents SET status = %s, error_message = %s, "
                "stage = NULL, updated_at = now() "
                "WHERE owner = %s AND status = %s "
                "AND updated_at < now() - make_interval(mins => %s)",
                (self.STATUS_FAILED, message, owner,
                 self.STATUS_PROCESSING, older_than_minutes),
            )
            conn.commit()
            return cur.rowcount

    def get_document_kb_id(self, document_id: str, owner: str | None = None) -> int | None:
        """返回文档当前所属知识库 id；文档不存在返回 None。

        以 documents 主表查询：pending/processing 文档可能还没有分块。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT knowledge_base_id FROM documents "
                "WHERE document_id = %s AND owner = %s LIMIT 1",
                (document_id, owner),
            ).fetchone()
        return row[0] if row else None
