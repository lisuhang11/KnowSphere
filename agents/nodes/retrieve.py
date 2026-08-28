"""prefetch_retrieval 节点：选定知识库时先检索再回答。"""

from __future__ import annotations

import json
import logging
import uuid

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from states import KnowSphereState
from tools.retrieval.doc_retrieval import _emit_citation_meta, _emit_thinking, doc_retrieval

logger = logging.getLogger(__name__)


def prefetch_retrieval(state: KnowSphereState, config: RunnableConfig) -> dict:
    """有 kb_ids 时用 rewrite_query 自动检索，将结果注入 ToolMessage。"""
    query = (state.get("rewrite_query") or state.get("current_query") or "").strip()
    if not query:
        return {}

    try:
        result = doc_retrieval.invoke({"query": query}, config=config)
    except Exception as exc:
        logger.warning("prefetch_retrieval 失败: %s", exc)
        return {}

    sources = result.get("sources") or []
    n = len(sources)
    _emit_thinking(
        f"【预检索】完成，已向模型注入 {n} 条片段"
        + (f"（检索词：{query}）" if query else ""),
        None,
    )
    if sources:
        try:
            from langgraph.config import get_stream_writer

            from schemas import Source

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

    tool_msg = ToolMessage(
        content=json.dumps(result, ensure_ascii=False),
        name="doc_retrieval",
        tool_call_id=f"prefetch-{uuid.uuid4().hex[:8]}",
    )
    return {"messages": [tool_msg]}
