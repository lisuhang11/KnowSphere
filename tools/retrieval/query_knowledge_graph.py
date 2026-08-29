"""query_knowledge_graph：从用户问题抽实体，在 Neo4j 中查一跳邻居，回捞相关 chunk。"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from models import create_chat_model
from schemas import RetrievalResult, Source
from schemas.graph import NameSpace
from stores.facade import ChunkStore
from stores.neo4j_repository import get_graph_repository
from utils.run_config import chat_model_kwargs_from_config, kb_ids_from_config

logger = logging.getLogger(__name__)

ENTITY_SYSTEM = """从用户问题中抽取关键实体名，只输出 JSON：
{"node":[{"name":"实体名"}]}
不要解释。若无明显实体，返回 {"node":[]}。
"""


def _parse_entity_names(text: str) -> list[str]:
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 回退：把整句当关键词
        return [text.strip()] if text.strip() else []
    names: list[str] = []
    for item in data.get("node") or data.get("nodes") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if name:
                names.append(name)
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


@tool
def query_knowledge_graph(
    query: str,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,  # noqa: B008
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> dict:
    """查询知识图谱，探索实体之间的关系，并返回关联文档片段。

    适合问「A 和 B 是什么关系」「某实体相关的人/组织/概念」等关系型问题。
    一般语义检索请用 doc_retrieval。仅当会话选择了知识库且库已启用图谱时有效。
    """
    kb_ids = kb_ids_from_config(config)
    if not kb_ids:
        return RetrievalResult(
            query=query,
            sources=[],
            note="未选择知识库：无法查询知识图谱。",
        ).model_dump()

    graph_repo = get_graph_repository()
    if not graph_repo.enabled:
        return RetrievalResult(
            query=query,
            sources=[],
            note="知识图谱未启用（NEO4J_ENABLE=false 或 Neo4j 不可用）。请改用 doc_retrieval。",
        ).model_dump()

    store = ChunkStore()
    kb_cfgs = store.get_knowledge_base_configs(kb_ids)
    graph_kb_ids = [kid for kid in kb_ids if kb_cfgs.get(kid, {}).get("graph_enabled")]
    if not graph_kb_ids:
        return RetrievalResult(
            query=query,
            sources=[],
            note="所选知识库均未启用知识图谱。请改用 doc_retrieval，或在知识库设置中开启图谱。",
        ).model_dump()

    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    _emit(writer, "正在从问题中抽取实体…")

    chat = create_chat_model(**chat_model_kwargs_from_config(config, {"temperature": 0.2}))
    resp = chat.invoke(
        [SystemMessage(content=ENTITY_SYSTEM), HumanMessage(content=query)]
    )
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    entities = _parse_entity_names(content)
    if not entities:
        entities = [query.strip()] if query.strip() else []
    _emit(writer, f"实体：{', '.join(entities)}；正在检索图谱…")

    all_chunk_ids: list[int] = []
    relation_notes: list[str] = []
    seen_chunk: set[int] = set()
    for kb_id in graph_kb_ids:
        g = graph_repo.search_node(NameSpace(knowledge_base=str(kb_id)), entities)
        for rel in g.relations[:20]:
            relation_notes.append(f"{rel.node1} -[{rel.type}]-> {rel.node2}")
        for node in g.nodes:
            for cid in node.chunks:
                try:
                    iid = int(cid)
                except (TypeError, ValueError):
                    continue
                if iid not in seen_chunk:
                    seen_chunk.add(iid)
                    all_chunk_ids.append(iid)

    if not all_chunk_ids:
        note = "图谱未命中相关实体。"
        if relation_notes:
            note += " 关系样例：" + "；".join(relation_notes[:5])
        return RetrievalResult(query=query, sources=[], note=note).model_dump()

    chunks = store.get_chunks_by_ids(all_chunk_ids[:30])
    sources: list[Source] = []
    for ch in chunks:
        snippet = (ch.get("content") or "")[:300]
        sources.append(
            Source(
                document_id=str(ch.get("document_id") or ""),
                file_name=str(ch.get("file_name") or (ch.get("metadata") or {}).get("source") or ""),
                chunk_index=int(ch.get("chunk_index") or 0),
                score=1.0,
                snippet=snippet,
            )
        )

    note = None
    if relation_notes:
        note = "图谱关系：" + "；".join(relation_notes[:8])
    return RetrievalResult(query=query, sources=sources, note=note).model_dump()


def _emit(writer: Any, text: str) -> None:
    try:
        if writer is None:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
        if writer is not None:
            writer({"type": "thinking", "content": text})
    except Exception:
        pass
