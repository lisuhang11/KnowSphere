"""分块写入与混合检索"""

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

class ChunkRepository:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ensureembedding_column(self, dim: int) -> None:
        """幂等确保 dim 维度的向量列与 HNSW 索引存在（非默认维度才需要）。

        在创建知识库时调用一次：列随 KB 的 embedding 模型维度固定下来，
        摄取/检索直接使用，避免热路径上的 ALTER。
        """
        if dim > MAX_HNSW_DIM:
            raise ValueError(
                f"维度 {dim} 超过 pgvector HNSW 索引上限 {MAX_HNSW_DIM}，"
                f"请换用低维 embedding 模型"
            )
        if dim == settings.embedding_dim:
            return
        col = embedding_column(dim)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"ALTER TABLE chunks ADD COLUMN IF NOT EXISTS {col} VECTOR({dim})"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS chunks_{col}_hnsw "
                f"ON chunks USING hnsw ({col} vector_cosine_ops)"
            )
            conn.commit()

    def insert_batch(
        self,
        document_id: str,
        file_name: str,
        chunks: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        owner: str | None = None,
        base_metadata: dict[str, Any] | None = None,
        kb_id: int | None = None,
        embedding_dim: int | None = None,
        metadata_list: Sequence[dict[str, Any]] | None = None,
    ) -> int:
        """批量写入分块。返回写入条数。kb_id 为目标知识库 ID。

        embedding_dim 决定写入哪一列：默认维度（settings.embedding_dim）写
        embedding 列，其他维度写 embedding_{dim} 列。
        metadata_list：可选，逐分块附加元数据（如切块策略的 context_header），
        与 base_metadata 合并（逐分块字段优先）。
        """
        owner = owner or get_current_owner() or settings.default_owner
        dim = embedding_dim or settings.embedding_dim
        col = embedding_column(dim)
        meta = dict(base_metadata or {})
        rows = []
        for i, (content, emb) in enumerate(zip(chunks, embeddings)):
            item_meta = dict(meta)
            if metadata_list and i < len(metadata_list):
                item_meta.update(metadata_list[i])
            item_meta["chunk_index"] = i
            rows.append(
                (
                    document_id,
                    file_name,
                    i,
                    content,
                    Jsonb(item_meta),
                    Vector(emb),
                    owner,
                    kb_id,
                )
            )
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO chunks "
                    f"(document_id, file_name, chunk_index, content, metadata, {col}, owner, knowledge_base_id) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
            conn.commit()
        return len(rows)

    def insert_parent_child_batch(
        self,
        document_id: str,
        file_name: str,
        parent_contents: Sequence[str],
        parent_metadata: Sequence[dict[str, Any]] | None,
        child_contents: Sequence[str],
        child_embeddings: Sequence[Sequence[float]],
        child_metadata: Sequence[dict[str, Any]] | None,
        child_parent_indices: Sequence[int],
        owner: str | None = None,
        base_metadata: dict[str, Any] | None = None,
        kb_id: int | None = None,
        embedding_dim: int | None = None,
    ) -> int:
        """写入父子分块：父块只存 DB，子块 embedding 后入库。返回子块条数。"""
        owner = owner or get_current_owner() or settings.default_owner
        dim = embedding_dim or settings.embedding_dim
        col = embedding_column(dim)
        meta = dict(base_metadata or {})
        parent_count = len(parent_contents)

        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            parent_ids: list[int | None] = []
            with conn.cursor() as cur:
                for i, content in enumerate(parent_contents):
                    item_meta = dict(meta)
                    if parent_metadata and i < len(parent_metadata):
                        item_meta.update(parent_metadata[i])
                    item_meta["chunk_index"] = i
                    cur.execute(
                        """
                        INSERT INTO chunks
                            (document_id, file_name, chunk_index, content, metadata,
                             chunk_type, owner, knowledge_base_id)
                        VALUES (%s, %s, %s, %s, %s, 'parent_text', %s, %s)
                        RETURNING id
                        """,
                        (
                            document_id,
                            file_name,
                            i,
                            content,
                            Jsonb(item_meta),
                            owner,
                            kb_id,
                        ),
                    )
                    parent_ids.append(cur.fetchone()[0])

                child_rows = []
                for i, (content, emb) in enumerate(zip(child_contents, child_embeddings)):
                    item_meta = dict(meta)
                    if child_metadata and i < len(child_metadata):
                        item_meta.update(child_metadata[i])
                    item_meta["chunk_index"] = parent_count + i
                    parent_index = (
                        child_parent_indices[i]
                        if i < len(child_parent_indices)
                        else -1
                    )
                    parent_db_id = (
                        parent_ids[parent_index]
                        if parent_index >= 0 and parent_index < len(parent_ids)
                        else None
                    )
                    child_rows.append(
                        (
                            document_id,
                            file_name,
                            parent_count + i,
                            content,
                            Jsonb(item_meta),
                            Vector(emb),
                            owner,
                            kb_id,
                            parent_db_id,
                        )
                    )
                if child_rows:
                    cur.executemany(
                        f"""
                        INSERT INTO chunks
                            (document_id, file_name, chunk_index, content, metadata,
                             {col}, owner, knowledge_base_id, chunk_type, parent_chunk_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'text', %s)
                        """,
                        child_rows,
                    )
            conn.commit()
        return len(child_contents)

    def replace_document_parent_child_batch(
        self,
        document_id: str,
        file_name: str,
        parent_contents: Sequence[str],
        parent_metadata: Sequence[dict[str, Any]] | None,
        child_contents: Sequence[str],
        child_embeddings: Sequence[Sequence[float]],
        child_metadata: Sequence[dict[str, Any]] | None,
        child_parent_indices: Sequence[int],
        owner: str | None = None,
        base_metadata: dict[str, Any] | None = None,
        kb_id: int | None = None,
        embedding_dim: int | None = None,
    ) -> int:
        """事务内删除旧分块后写入父子分块（reparse 用）。"""
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "DELETE FROM chunks WHERE document_id = %s AND owner = %s",
                (document_id, owner),
            )
            conn.execute(
                "UPDATE documents SET updated_at = now() "
                "WHERE document_id = %s AND owner = %s",
                (document_id, owner),
            )
            conn.commit()
        return self.insert_parent_child_batch(
            document_id=document_id,
            file_name=file_name,
            parent_contents=parent_contents,
            parent_metadata=parent_metadata,
            child_contents=child_contents,
            child_embeddings=child_embeddings,
            child_metadata=child_metadata,
            child_parent_indices=child_parent_indices,
            owner=owner,
            base_metadata=base_metadata,
            kb_id=kb_id,
            embedding_dim=embedding_dim,
        )

    def get_chunks_by_ids(
        self, chunk_ids: Sequence[int], owner: str | None = None
    ) -> list[dict[str, Any]]:
        """按主键批量取分块（parent resolve 用）。"""
        if not chunk_ids:
            return []
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, document_id, chunk_index, content, metadata, chunk_type, parent_chunk_id
                FROM chunks
                WHERE id = ANY(%s) AND owner = %s
                """,
                (list(chunk_ids), owner),
            ).fetchall()
        return [
            {
                "id": r[0],
                "document_id": r[1],
                "chunk_index": r[2],
                "content": r[3],
                "metadata": load_jsonb(r[4]),
                "chunk_type": r[5],
                "parent_chunk_id": r[6],
            }
            for r in rows
        ]

    def replace_document_chunks(
        self,
        document_id: str,
        file_name: str,
        chunks: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        owner: str | None = None,
        base_metadata: dict[str, Any] | None = None,
        kb_id: int | None = None,
        embedding_dim: int | None = None,
        metadata_list: Sequence[dict[str, Any]] | None = None,
    ) -> int:
        """事务内「删除该文档旧分块 → 写入新分块」，供重新解析（reparse）使用。

        与 insert_batch 同构，但先按 document_id 清空旧分块，保证换配置重切后
        不残留旧块；成功后更新 documents 行的 updated_at。
        """
        owner = owner or get_current_owner() or settings.default_owner
        dim = embedding_dim or settings.embedding_dim
        col = embedding_column(dim)
        meta = dict(base_metadata or {})
        rows = []
        for i, (content, emb) in enumerate(zip(chunks, embeddings)):
            item_meta = dict(meta)
            if metadata_list and i < len(metadata_list):
                item_meta.update(metadata_list[i])
            item_meta["chunk_index"] = i
            rows.append(
                (
                    document_id,
                    file_name,
                    i,
                    content,
                    Jsonb(item_meta),
                    Vector(emb),
                    owner,
                    kb_id,
                )
            )
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunks WHERE document_id = %s AND owner = %s",
                    (document_id, owner),
                )
                cur.executemany(
                    f"INSERT INTO chunks "
                    f"(document_id, file_name, chunk_index, content, metadata, {col}, owner, knowledge_base_id) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
                cur.execute(
                    "UPDATE documents SET updated_at = now() "
                    "WHERE document_id = %s AND owner = %s",
                    (document_id, owner),
                )
            conn.commit()
        return len(rows)

    def hybrid_search(
        self,
        query_text: str,
        query_embedding: Sequence[float],
        top_k: int | None = None,
        owner: str | None = None,
        rrf_k: int = 60,
        kb_ids: Sequence[int] | None = None,
        embedding_dim: int | None = None,
        include_embedding: bool = True,
    ) -> list[dict[str, Any]]:
        """混合检索：向量余弦 + pg_trgm 词法分别取候选，RRF(rank 级)融合后取 top_k。

        RRF 相比线性加权不依赖两路分数同量纲：score = Σ 1/(k + rank)，跨量纲更稳。

        kb_ids：检索范围限定在这些知识库内（合并检索，不分组）；
        None/空 = 全部知识库（knowledge_base_id IS NOT NULL，
        无库归属的存量数据彻底忽略，不参与检索）。

        embedding_dim：目标知识库的 embedding 维度，决定比较哪一列
        （默认维度用 embedding 列，其他维度用 embedding_{dim} 列）。
        """
        top_k = top_k or settings.retrieval_top_k
        owner = owner or get_current_owner() or settings.default_owner
        dim = embedding_dim or settings.embedding_dim
        vec_col = embedding_column(dim)
        # 每路多取一些候选，融合排序后再截断到 top_k
        candidate_n = max(top_k * 4, rrf_k)
        if kb_ids:
            kb_cond = "knowledge_base_id = ANY(%s)"
            kb_params: list[Any] = [list(kb_ids)]
        else:
            kb_cond = "knowledge_base_id IS NOT NULL"
            kb_params = []
        # embedding 列仅供 MMR 多样性选择使用；关闭时省去高维向量的传输与反序列化
        emb_col = f", {vec_col}" if include_embedding else ""
        retrievable = RETRIEVABLE_CHUNK_WHERE
        vec_sql = f"""
            SELECT id, document_id, file_name, chunk_index, content, metadata,
                   parent_chunk_id{emb_col},
                   (1 - ({vec_col} <=> %s)) AS score
            FROM chunks
            WHERE owner = %s AND {kb_cond} AND {retrievable} AND {vec_col} IS NOT NULL
            ORDER BY score DESC
            LIMIT %s
        """
        lex_sql = f"""
            SELECT id, document_id, file_name, chunk_index, content, metadata,
                   parent_chunk_id{emb_col},
                   similarity(content, %s) AS score
            FROM chunks
            WHERE owner = %s AND {kb_cond} AND {retrievable}
            ORDER BY score DESC
            LIMIT %s
        """
        conn = psycopg.connect(self.dsn)
        try:
            register_vector(conn)
            rows = {"vec": [], "lex": []}
            score_i = 8 if include_embedding else 7
            for kind, sql, params in (
                ("vec", vec_sql, [Vector(query_embedding), owner, *kb_params, candidate_n]),
                ("lex", lex_sql, [query_text, owner, *kb_params, candidate_n]),
            ):
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows[kind] = [
                        {
                            "id": r[0],
                            "document_id": r[1],
                            "file_name": r[2],
                            "chunk_index": r[3],
                            "content": r[4],
                            "metadata": r[5],
                            "parent_chunk_id": r[6],
                            "embedding": r[7] if include_embedding else None,
                            "vec_score": float(r[score_i]) if kind == "vec" else None,
                            "lex_score": float(r[score_i]) if kind == "lex" else None,
                        }
                        for r in cur.fetchall()
                    ]
        finally:
            conn.close()

        # RRF 融合：两路 rank 各自打分，同名(chunk)累加
        # （公共实现，doc_retrieval 的多路/多库融合同样复用）
        results = []
        for r in rrf_fuse([rows["vec"], rows["lex"]], top_k, rrf_k):
            results.append(
                {
                    "id": r.get("id"),
                    "document_id": r["document_id"],
                    "file_name": r["file_name"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "metadata": r["metadata"],
                    "parent_chunk_id": r.get("parent_chunk_id"),
                    "snippet": r["content"][:300],
                    # 保留原 score 字段语义（引用展示用）
                    "score": r["score"],
                    "vec_score": r["vec_score"],
                    "lex_score": r["lex_score"],
                    "rrf": round(r["rrf"], 4),
                    "embedding": r["embedding"],
                }
            )
        return results

    def list_chunks(
        self,
        document_id: str,
        owner: str | None = None,
        page: int = 1,
        page_size: int = 20,
        include_parent_text: bool = False,
    ) -> dict[str, Any]:
        """分页返回某文档的分块（按 chunk_index 升序）。

        include_parent_text=False 时只返回可检索子块（chunk_type=text），
        父子分块模式下默认隐藏 parent_text 父块。
        """
        owner = owner or get_current_owner() or settings.default_owner
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size
        type_cond = "" if include_parent_text else " AND chunk_type = 'text'"
        with psycopg.connect(self.dsn) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM chunks WHERE document_id = %s AND owner = %s{type_cond}",
                (document_id, owner),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, chunk_index, content, metadata, chunk_type, parent_chunk_id, created_at
                FROM chunks
                WHERE document_id = %s AND owner = %s{type_cond}
                ORDER BY chunk_index ASC
                LIMIT %s OFFSET %s
                """,
                (document_id, owner, page_size, offset),
            ).fetchall()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "chunks": [
                {
                    "id": r[0],
                    "chunk_index": r[1],
                    "content": r[2],
                    "metadata": r[3],
                    "chunk_type": r[4],
                    "parent_chunk_id": r[5],
                    "char_count": len(r[2]),
                    "token_count": estimate_tokens(r[2]),
                    "created_at": r[6].isoformat() if r[6] else None,
                }
                for r in rows
            ],
        }

    def get_document_chunks_content(
        self, document_id: str, owner: str | None = None
    ) -> list[str]:
        """返回文档全部分块内容（按 chunk_index 排序）。

        用于跨 embedding 模型移动文档时按目标库配置重新嵌入。
        """
        owner = owner or get_current_owner() or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT content FROM chunks "
                "WHERE document_id = %s AND owner = %s AND chunk_type = 'text' "
                "ORDER BY chunk_index",
                (document_id, owner),
            ).fetchall()
        return [r[0] for r in rows]
