"""doc_retrieval：知识库混合检索工具，返回带来源的片段列表。

流程委托 services.retrieval.RetrievalService；本模块保留 LangGraph tool 壳与流式 thinking。
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from config.settings import settings
from schemas import RetrievalResult
from utils.citation import citation_meta_payload, citations_from_sources
from utils.run_config import kb_ids_from_config


@tool
def doc_retrieval(
    query: str,
    top_k: int = 0,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,  # noqa: B008
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict:
    """在用户上传的知识库文档中检索与 query 最相关的片段，返回带文件来源的结果。

    回答基于知识库的问题时先调用本工具，回答必须基于检索到的片段。
    注意：仅当会话指定了知识库时才会检索；未指定知识库时返回空结果并附说明，
    此时应直接基于自身知识回答，不要编造文档来源。
    """
    from services.deps import get_retrieval_service

    _retrieval = get_retrieval_service()
    kb_ids = kb_ids_from_config(config)
    if not kb_ids:
        return RetrievalResult(
            query=query,
            sources=[],
            note=(
                "未选择知识库：无法检索用户文档。请提示用户在输入框上方选择知识库后再问；"
                "不要编造文档来源，也不要用公开资料替代用户文档作答。"
            ),
        ).model_dump()

    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None

    def on_thinking(text: str) -> None:
        _emit_thinking(text, writer)

    result = _retrieval.search(
        query=query,
        kb_ids=kb_ids,
        top_k=top_k,
        config=config,
        on_thinking=on_thinking,
    )
    _emit_citation_meta(result.sources, writer)
    return result.model_dump()


def _emit_citation_meta(sources: list, writer: Any = None) -> None:
    if not settings.citation_enabled or not sources:
        return
    try:
        if writer is None:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        if writer is not None:
            writer(citation_meta_payload(citations_from_sources(sources)))
    except Exception:
        pass


def _emit_thinking(text: str, writer: Any = None) -> None:
    try:
        if writer is None:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        if writer is not None:
            writer({"type": "thinking", "content": text})
    except Exception:
        pass
