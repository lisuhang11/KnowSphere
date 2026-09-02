"""retrieve 节点：选定知识库且意图需要检索时，跑混合检索并写入 last_sources。"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from schemas import Source
from states import KnowSphereState
from tools.retrieval.doc_retrieval import _emit_citation_meta, _emit_thinking, doc_retrieval

logger = logging.getLogger(__name__)


def retrieve(state: KnowSphereState, config: RunnableConfig) -> dict:
    """用 rewrite_query 检索，结果进 last_sources（不伪装成 ToolMessage）。"""
    query = (state.get("rewrite_query") or state.get("current_query") or "").strip()
    if not query:
        return {"last_sources": [], "retrieval_note": ""}

    try:
        result = doc_retrieval.invoke({"query": query}, config=config)
    except Exception as exc:
        logger.warning("retrieve 失败: %s", exc)
        return {"last_sources": [], "retrieval_note": ""}

    sources = result.get("sources") or []
    note = str(result.get("note") or "")
    n = len(sources)
    _emit_thinking(
        f"【检索】完成，命中 {n} 条片段"
        + (f"（检索词：{query}）" if query else ""),
        None,
    )
    if sources:
        try:
            from langgraph.config import get_stream_writer

            typed = [
                Source(
                    document_id=s["document_id"],
                    file_name=s["file_name"],
                    chunk_index=s["chunk_index"],
                    score=s.get("score", 0.0),
                    snippet=s.get("snippet", ""),
                )
                for s in sources
            ]
            _emit_citation_meta(typed, get_stream_writer())
        except Exception:
            pass

    return {"last_sources": sources, "retrieval_note": note}
