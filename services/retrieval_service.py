"""检索业务编排：多库分组召回 → 扩展 → 父块回捞 → 精排 → MMR。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from langchain_core.runnables import RunnableConfig

from config.settings import settings
from models import create_chat_model, create_embeddings, create_reranker
from pydantic import BaseModel, Field
from schemas import RetrievalResult, Source
from stores.facade import ChunkStore
from stores.rrf import rrf_fuse
from tools.retrieval.parent_resolve import resolve_parent_chunks
from tools.retrieval.query_expansion import expand_queries_local
from tools.retrieval.thinking_format import (
    format_expansion_result,
    format_multi_query_result,
    format_multi_query_trigger,
    format_recall_result,
    format_recall_start,
    format_source_preview,
)
from utils.run_config import chat_model_kwargs_from_config

logger = logging.getLogger(__name__)

_MQ_LLM_KWARGS: dict[str, Any] = {
    "temperature": 0,
    "extra_body": {"enable_thinking": False},
}

_MQ_PROMPT = (
    "You are a search query rewriter for a document RAG system. The user's original "
    "query may be multi-hop, i.e. answering it requires evidence from multiple documents "
    "or passages. Rewrite it into {n} distinct search queries in the SAME language as the "
    "original query. Each query should target a different aspect or sub-question, so that "
    "combined retrieval can cover all the evidence needed. Only output the rewritten "
    "queries, no explanations.\n\nOriginal query: {query}"
)

class _SubQueries(BaseModel):
    queries: list[str] = Field(description="不同检索角度的子查询列表")

class RetrievalService:
    def __init__(self, store: ChunkStore() | None = None) -> None:
        self.store = store or ChunkStore()

    def search(
        self,
        query: str,
        kb_ids: list[int],
        top_k: int | None = None,
        config: RunnableConfig | None = None,
        on_thinking: Callable[[str], None] | None = None,
    ) -> RetrievalResult:
        k = top_k if top_k and top_k > 0 else settings.retrieval_top_k
        need_pool = settings.rerank_enabled or settings.mmr_enabled
        recall_k = settings.retrieval_candidate_k if need_pool else k

        kb_configs = self.store.get_knowledge_base_configs(kb_ids)
        if not kb_configs:
            return RetrievalResult(
                query=query,
                sources=[],
                note=(
                    f"所选知识库不存在（ids={kb_ids}）：无法检索用户文档，"
                    "请提示用户检查知识库选择，不要凭公开资料作答。"
                ),
            )

        kbs_by_model: dict[tuple[str, int], list[int]] = {}
        for kb_id, kb in kb_configs.items():
            kbs_by_model.setdefault((kb["embedding_model_id"], kb["embedding_dim"]), []).append(kb_id)

        expansion_threshold = max(1, recall_k // 2)
        # 首轮：只用改写后的主检索词单路召回；命中不足再触发 LLM 多跳 / 本地扩展
        queries = [query]
        self._think(
            on_thinking,
            format_recall_start(query, queries, [], recall_k, expansion_threshold),
        )
        rows = self._recall(queries, recall_k, kbs_by_model)
        before_expand = len(rows)
        self._think(
            on_thinking,
            format_recall_result(before_expand, recall_k, expansion_threshold),
        )

        if settings.multi_query_enabled and before_expand < expansion_threshold:
            mq_subs = self._generate_sub_queries(query, settings.multi_query_count, config)
            if mq_subs:
                self._think(
                    on_thinking,
                    format_multi_query_trigger(before_expand, expansion_threshold, mq_subs),
                )
                mq_rows = self._recall(mq_subs, recall_k, kbs_by_model)
                if mq_rows:
                    merged = rrf_fuse([rows, mq_rows], recall_k)
                    self._think(
                        on_thinking,
                        format_multi_query_result(len(mq_rows), before_expand, len(merged)),
                    )
                    rows = merged
                    before_expand = len(rows)
                else:
                    self._think(on_thinking, "多跳补搜无额外命中，沿用首轮结果。")
            else:
                self._think(on_thinking, "多跳子查询生成失败或为空，沿用首轮结果。")

        if settings.query_expansion_enabled and before_expand < expansion_threshold:
            variants = expand_queries_local(query, settings.query_expansion_max_variants)
            variants = [v for v in variants if v.strip().lower() != query.strip().lower()]
            if variants:
                exp_recall_k = max(recall_k * 2, k)
                exp_rows = self._recall(variants, exp_recall_k, kbs_by_model)
                if exp_rows:
                    after_before = len(rows)
                    rows = rrf_fuse([rows, exp_rows], recall_k)
                    self._think(
                        on_thinking,
                        format_expansion_result(variants, len(exp_rows), after_before, len(rows)),
                    )
            else:
                self._think(on_thinking, "本地扩展：未生成可用变体，沿用当前结果。")
        elif not settings.query_expansion_enabled and before_expand < expansion_threshold:
            self._think(on_thinking, "本地扩展已关闭，沿用当前结果。")

        rows = resolve_parent_chunks(rows, self.store)
        resolved_n = sum(1 for r in rows if r.get("parent_resolved"))
        if resolved_n:
            self._think(on_thinking, f"父块扩展：{resolved_n} 条子块命中已回捞父块上下文。")

        pool_before_rerank = len(rows)
        if settings.rerank_enabled and len(rows) > k:
            top_n = len(rows) if settings.mmr_enabled else k
            rows = self._rerank_rows(query, rows, top_n)
            self._think(
                on_thinking,
                f"【4/5 精排】{pool_before_rerank} 条候选 → 保留 {len(rows)} 条（rerank 主 query：{query}）",
            )

        pool_before_mmr = len(rows)
        if settings.mmr_enabled and len(rows) > k:
            try:
                rows = self._mmr_select(rows, k)
                self._think(
                    on_thinking,
                    f"【5/5 多样性选择】{pool_before_mmr} 条 → 最终 {len(rows)} 条",
                )
            except Exception as exc:
                logger.warning("MMR 失败，降级原序: %s", exc)
                self._think(on_thinking, f"多样性选择失败，降级原序（{exc}）。")

        rows = rows[:k]
        if rows:
            doc_n = len({r["document_id"] for r in rows})
            preview = format_source_preview(rows)
            self._think(
                on_thinking,
                f"检索完成：{len(rows)} 条片段，来自 {doc_n} 篇文档\n{preview}",
            )
        else:
            self._think(on_thinking, "检索完成：未命中任何片段。")

        return RetrievalResult(
            query=query,
            sources=[
                Source(
                    document_id=r["document_id"],
                    file_name=r["file_name"],
                    chunk_index=r["chunk_index"],
                    score=r["score"],
                    snippet=r["snippet"],
                    parent_resolved=bool(r.get("parent_resolved")),
                    sub_chunk_index=r.get("sub_chunk_index"),
                )
                for r in rows
            ],
        )

    @staticmethod
    def _think(on_thinking: Callable[[str], None] | None, text: str) -> None:
        if on_thinking is not None:
            on_thinking(text)

    def _recall(
        self,
        queries: list[str],
        recall_k: int,
        kbs_by_model: dict[tuple[str, int], list[int]],
    ) -> list[dict[str, Any]]:
        ranked: list[list[dict[str, Any]]] = []
        for (model_id, dim), ids in kbs_by_model.items():
            embeddings = create_embeddings(model=model_id)
            for q in queries:
                vec = embeddings.embed_query(q)
                ranked.append(
                    self.store.hybrid_search(
                        q,
                        vec,
                        top_k=recall_k,
                        kb_ids=ids,
                        embedding_dim=dim,
                        include_embedding=settings.mmr_enabled,
                    )
                )
        return rrf_fuse(ranked, recall_k)

    def _generate_sub_queries(
        self,
        query: str,
        n: int,
        config: RunnableConfig | None = None,
    ) -> list[str]:
        try:
            llm = create_chat_model(
                **chat_model_kwargs_from_config(config, _MQ_LLM_KWARGS),
            ).with_structured_output(_SubQueries)
            out = llm.invoke(_MQ_PROMPT.format(query=query, n=n), config={"callbacks": []})
            seen = {query.strip().lower()}
            uniq = []
            for q in (getattr(out, "queries", None) or []):
                q = (q or "").strip()
                key = q.lower()
                if q and key not in seen:
                    seen.add(key)
                    uniq.append(q)
            return uniq[:n]
        except Exception as exc:
            logger.warning("multi_query 生成失败，降级单查询: %s", exc)
            return []

    def _rerank_rows(self, query: str, rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
        try:
            reranker = create_reranker()
            hits = reranker.rerank(query, [r["content"] for r in rows], top_n=top_n)
            ranked = []
            for h in hits:
                row = dict(rows[h["index"]])
                row["score"] = round(h["relevance_score"], 4)
                row["rerank_score"] = round(h["relevance_score"], 4)
                ranked.append(row)
            return ranked
        except Exception as exc:
            logger.warning("rerank 失败，降级 RRF 序: %s", exc)
            return rows[:top_n]

    def _token_jaccard_matrix(self, contents: list[str]) -> np.ndarray:
        def _tokens(text: str) -> set[str]:
            t = (text or "").lower()
            return {t[i : i + 2] for i in range(len(t) - 1)}

        toks = [_tokens(c) for c in contents]
        n = len(toks)
        mat = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            a = toks[i]
            for j in range(i + 1, n):
                b = toks[j]
                s = len(a & b) / len(a | b) if a and b else 0.0
                mat[i, j] = mat[j, i] = s
        np.fill_diagonal(mat, 1.0)
        return mat

    def _mmr_select(self, rows: list[dict[str, Any]], k: int, lam: float | None = None) -> list[dict[str, Any]]:
        if len(rows) <= k:
            return rows
        lam = settings.mmr_lambda if lam is None else lam
        jaccard_w = getattr(settings, "mmr_jaccard_weight", 0.0) or 0.0

        def _to_list(e: Any) -> list[float] | None:
            if e is None:
                return None
            if hasattr(e, "to_list"):
                return e.to_list
            return list(e)

        valid = [r for r in rows if _to_list(r.get("embedding")) is not None]
        if len(valid) <= k:
            return rows

        embs = np.array([_to_list(r["embedding"]) for r in valid], dtype=np.float32)
        unit = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-12)
        cross = unit @ unit.T
        if jaccard_w > 0:
            cross = (1 - jaccard_w) * cross + jaccard_w * self._token_jaccard_matrix(
                [r["content"] for r in valid]
            )

        scores = np.array([float(r["score"]) for r in valid])
        lo, hi = scores.min, scores.max
        rel = (scores - lo) / (hi - lo + 1e-12)

        n = len(valid)
        max_red = np.zeros(n)
        selected: list[int] = []
        remaining = list(range(n))
        while len(selected) < k and remaining:
            if not selected:
                best = max(remaining, key=lambda i: rel[i])
            else:
                best = max(remaining, key=lambda i: lam * rel[i] - (1 - lam) * max_red[i])
            selected.append(best)
            remaining.remove(best)
            if remaining:
                max_red = np.maximum(max_red, cross[best])

        if len(selected) >= 2:
            sub = cross[np.ix_(selected, selected)]
            avg_red = float(sub[np.triu_indices(len(selected), 1)].mean)
            logger.info(
                "MMR 选取 %d 条（候选 %d，λ=%.2f，jaccard_w=%.2f），平均成对冗余 %.3f",
                len(selected),
                n,
                lam,
                jaccard_w,
                avg_red,
            )
        return [valid[i] for i in selected]
