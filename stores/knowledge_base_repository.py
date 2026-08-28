"""知识库 CRUD"""

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

class KnowledgeBaseRepository:
    def __init__(self, dsn: str, chunks: "ChunkRepository") -> None:
        self.dsn = dsn
        self._chunks = chunks

    def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        owner: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        embedding_model_id: str | None = None,
        embedding_dim: int | None = None,
        chunk_strategy: str | None = None,
        summary_model_id: str | None = None,
        enable_parent_child: bool | None = None,
        parent_chunk_size: int | None = None,
        child_chunk_size: int | None = None,
    ) -> dict[str, Any]:
        """创建知识库。分块/嵌入参数缺省时用全局配置；embedding 创建后不可修改。

        非默认维度会幂等建好 chunks 的向量列与 HNSW 索引（随库固定）。
        """
        owner = owner or get_current_owner() or settings.default_owner
        chunk_size = chunk_size or settings.chunk_size
        chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
        embedding_model_id = embedding_model_id or settings.embedding_model
        embedding_dim = embedding_dim or settings.embedding_dim
        chunk_strategy = chunk_strategy or "auto"
        enable_parent_child = (
            settings.enable_parent_child
            if enable_parent_child is None
            else enable_parent_child
        )
        parent_chunk_size = parent_chunk_size or settings.parent_chunk_size
        child_chunk_size = child_chunk_size or settings.child_chunk_size
        if embedding_dim > MAX_HNSW_DIM:
            raise ValueError(
                f"维度 {embedding_dim} 超过 pgvector HNSW 索引上限 {MAX_HNSW_DIM}，"
                f"请换用低维 embedding 模型"
            )
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                INSERT INTO knowledge_bases
                    (name, description, owner, chunk_size, chunk_overlap,
                     embedding_model_id, embedding_dim, chunk_strategy, summary_model_id,
                     enable_parent_child, parent_chunk_size, child_chunk_size)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {KB_COLS}
                """,
                (
                    name, description, owner, chunk_size, chunk_overlap,
                    embedding_model_id, embedding_dim, chunk_strategy, summary_model_id,
                    enable_parent_child, parent_chunk_size, child_chunk_size,
                ),
            ).fetchone()
            conn.commit()
        self._chunks.ensureembedding_column(embedding_dim)
        return kb_row_to_dict(row)

    def list_knowledge_bases(self, owner: str | None = None) -> list[dict[str, Any]]:
        """列出全部知识库，带文档数/分块数统计与最近更新时间。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {kb_cols_prefixed('kb')},
                       COUNT(DISTINCT ch.document_id) AS document_count,
                       COUNT(ch.id) AS chunk_count
                FROM knowledge_bases kb
                LEFT JOIN chunks ch ON ch.knowledge_base_id = kb.id AND ch.owner = kb.owner
                WHERE kb.owner = %s
                GROUP BY kb.id
                ORDER BY kb.updated_at DESC
                """,
                (owner,),
            ).fetchall()
        result = []
        for r in rows:
            kb = kb_row_to_dict(r[:KB_COL_COUNT])
            kb["document_count"] = r[KB_COL_COUNT]
            kb["chunk_count"] = r[KB_COL_COUNT + 1]
            result.append(kb)
        return result

    def get_knowledge_base(self, kb_id: int, owner: str | None = None) -> dict[str, Any] | None:
        """取单个知识库（含统计）。不存在返回 None。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {kb_cols_prefixed('kb')},
                       COUNT(DISTINCT ch.document_id) AS document_count,
                       COUNT(ch.id) AS chunk_count
                FROM knowledge_bases kb
                LEFT JOIN chunks ch ON ch.knowledge_base_id = kb.id AND ch.owner = kb.owner
                WHERE kb.id = %s AND kb.owner = %s
                GROUP BY kb.id
                """,
                (kb_id, owner),
            ).fetchone()
        if not row:
            return None
        kb = kb_row_to_dict(row[:KB_COL_COUNT])
        kb["document_count"] = row[KB_COL_COUNT]
        kb["chunk_count"] = row[KB_COL_COUNT + 1]
        return kb

    def get_knowledge_base_configs(
        self, kb_ids: Sequence[int], owner: str | None = None
    ) -> dict[int, dict[str, Any]]:
        """批量取知识库配置（不带文档/分块聚合统计），供检索热路径使用。

        返回 {kb_id: kb_dict}，仅含存在的库；避免检索时对每个库做聚合计数
        （get_knowledge_base 的 COUNT 用于管理页展示，不适合检索循环）。
        """
        if not kb_ids:
            return {}
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT {KB_COLS} FROM knowledge_bases "
                "WHERE id = ANY(%s) AND owner = %s",
                (list(kb_ids), owner),
            ).fetchall()
        return {r[0]: kb_row_to_dict(r) for r in rows}

    def update_knowledge_base(
        self,
        kb_id: int,
        name: str | None = None,
        description: str | None = None,
        owner: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_strategy: str | None = None,
        summary_model_id: str | None | object = None,
        enable_parent_child: bool | None = None,
        parent_chunk_size: int | None = None,
        child_chunk_size: int | None = None,
    ) -> dict[str, Any] | None:
        """更新知识库名称/描述/分块参数/摘要模型（None 表示不修改）。不存在返回 None。

        embedding 模型创建后禁止修改（Q8），此处不提供该字段。
        summary_model_id 传空字符串表示清空。
        """
        owner = owner or get_current_owner() or settings.default_owner
        summary_sql = "summary_model_id = COALESCE(%s, summary_model_id),"
        summary_arg: str | None = None
        if summary_model_id is not None:
            if summary_model_id == "":
                summary_sql = "summary_model_id = NULL,"
                summary_arg = None
            else:
                summary_sql = "summary_model_id = %s,"
                summary_arg = str(summary_model_id)
        with psycopg.connect(self.dsn) as conn:
            if summary_model_id is not None and summary_model_id != "":
                row = conn.execute(
                    f"""
                    UPDATE knowledge_bases
                    SET name = COALESCE(%s, name),
                        description = COALESCE(%s, description),
                        chunk_size = COALESCE(%s, chunk_size),
                        chunk_overlap = COALESCE(%s, chunk_overlap),
                        chunk_strategy = COALESCE(%s, chunk_strategy),
                        enable_parent_child = COALESCE(%s, enable_parent_child),
                        parent_chunk_size = COALESCE(%s, parent_chunk_size),
                        child_chunk_size = COALESCE(%s, child_chunk_size),
                        {summary_sql}
                        updated_at = now()
                    WHERE id = %s AND owner = %s
                    RETURNING {KB_COLS}
                    """,
                    (
                        name,
                        description,
                        chunk_size,
                        chunk_overlap,
                        chunk_strategy,
                        enable_parent_child,
                        parent_chunk_size,
                        child_chunk_size,
                        summary_arg,
                        kb_id,
                        owner,
                    ),
                ).fetchone()
            elif summary_model_id == "":
                row = conn.execute(
                    f"""
                    UPDATE knowledge_bases
                    SET name = COALESCE(%s, name),
                        description = COALESCE(%s, description),
                        chunk_size = COALESCE(%s, chunk_size),
                        chunk_overlap = COALESCE(%s, chunk_overlap),
                        chunk_strategy = COALESCE(%s, chunk_strategy),
                        enable_parent_child = COALESCE(%s, enable_parent_child),
                        parent_chunk_size = COALESCE(%s, parent_chunk_size),
                        child_chunk_size = COALESCE(%s, child_chunk_size),
                        summary_model_id = NULL,
                        updated_at = now()
                    WHERE id = %s AND owner = %s
                    RETURNING {KB_COLS}
                    """,
                    (
                        name, description, chunk_size, chunk_overlap, chunk_strategy,
                        enable_parent_child, parent_chunk_size, child_chunk_size,
                        kb_id, owner,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    UPDATE knowledge_bases
                    SET name = COALESCE(%s, name),
                        description = COALESCE(%s, description),
                        chunk_size = COALESCE(%s, chunk_size),
                        chunk_overlap = COALESCE(%s, chunk_overlap),
                        chunk_strategy = COALESCE(%s, chunk_strategy),
                        enable_parent_child = COALESCE(%s, enable_parent_child),
                        parent_chunk_size = COALESCE(%s, parent_chunk_size),
                        child_chunk_size = COALESCE(%s, child_chunk_size),
                        updated_at = now()
                    WHERE id = %s AND owner = %s
                    RETURNING {KB_COLS}
                    """,
                    (
                        name, description, chunk_size, chunk_overlap, chunk_strategy,
                        enable_parent_child, parent_chunk_size, child_chunk_size,
                        kb_id, owner,
                    ),
                ).fetchone()
            conn.commit()
        return kb_row_to_dict(row) if row else None

    def delete_knowledge_base(self, kb_id: int, owner: str | None = None) -> dict[str, Any] | None:
        """删除知识库及其全部文档分块（同一事务）。返回删除统计。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            kb = conn.execute(
                f"SELECT {KB_COLS} "
                "FROM knowledge_bases WHERE id = %s AND owner = %s",
                (kb_id, owner),
            ).fetchone()
            if not kb:
                return None
            stats = conn.execute(
                """
                SELECT COUNT(DISTINCT document_id), COUNT(*) FROM chunks
                WHERE knowledge_base_id = %s AND owner = %s
                """,
                (kb_id, owner),
            ).fetchone()
            conn.execute(
                "DELETE FROM chunks WHERE knowledge_base_id = %s AND owner = %s",
                (kb_id, owner),
            )
            conn.execute(
                "DELETE FROM documents WHERE knowledge_base_id = %s AND owner = %s",
                (kb_id, owner),
            )
            conn.execute("DELETE FROM knowledge_bases WHERE id = %s", (kb_id,))
            conn.commit()
        return {
            **kb_row_to_dict(kb),
            "deleted_documents": stats[0],
            "deleted_chunks": stats[1],
        }

    def move_document(
        self, document_id: str, kb_id: int, owner: str | None = None
    ) -> int:
        """把文档（全部分块）移动到目标知识库。返回受影响分块数。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute(
                """
                UPDATE chunks SET knowledge_base_id = %s
                WHERE document_id = %s AND owner = %s
                """,
                (kb_id, document_id, owner),
            )
            conn.execute(
                """
                UPDATE documents SET knowledge_base_id = %s, updated_at = now()
                WHERE document_id = %s AND owner = %s
                """,
                (kb_id, document_id, owner),
            )
            conn.commit()
            return cur.rowcount
