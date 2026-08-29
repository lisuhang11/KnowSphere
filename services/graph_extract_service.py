"""Chunk 级实体关系抽取：LLM → GraphData → Neo4j。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from models import create_chat_model
from schemas.graph import GraphData, GraphNode, GraphRelation, NameSpace
from stores.facade import ChunkStore
from stores.neo4j_repository import get_graph_repository

logger = logging.getLogger(__name__)

DEFAULT_RELATION_TAGS = ["相关", "属于", "包含", "别名", "位于", "使用", "依赖"]

EXTRACT_SYSTEM = """你是信息抽取助手。根据给定文本抽取实体与关系，只输出 JSON，不要解释。

输出格式严格为：
{{
  "node": [{{"name": "实体名", "attributes": ["属性1", "属性2"]}}],
  "relation": [{{"node1": "实体A", "node2": "实体B", "type": "关系类型"}}]
}}

规则：
1. 实体名尽量用原文中的规范称谓；attributes 只写文本中明确出现的属性。
2. 关系类型只能从以下列表中选择：{tags}
3. 不要编造文本中不存在的实体或关系；没有可抽内容时返回 {{"node":[],"relation":[]}}。
"""


def _parse_graph_json(text: str) -> GraphData:
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
        logger.warning("failed to parse graph JSON: %s", text[:200])
        return GraphData()

    nodes: list[GraphNode] = []
    for item in data.get("node") or data.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        attrs = item.get("attributes") or []
        if not isinstance(attrs, list):
            attrs = [str(attrs)]
        nodes.append(GraphNode(name=name, attributes=[str(a) for a in attrs if a]))

    relations: list[GraphRelation] = []
    for item in data.get("relation") or data.get("relations") or []:
        if not isinstance(item, dict):
            continue
        n1 = str(item.get("node1") or "").strip()
        n2 = str(item.get("node2") or "").strip()
        typ = str(item.get("type") or "").strip()
        if n1 and n2 and typ:
            relations.append(GraphRelation(node1=n1, node2=n2, type=typ))

    return GraphData(nodes=nodes, relations=relations)


class GraphExtractService:
    def __init__(self, store: ChunkStore | None = None) -> None:
        self.store = store or ChunkStore()
        self.graph = get_graph_repository()

    def extract_chunk(
        self,
        *,
        chunk_id: int,
        kb_id: int,
        document_id: str,
        model_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.graph.enabled:
            return {"skipped": "neo4j_disabled"}

        rows = self.store.get_chunks_by_ids([chunk_id])
        if not rows:
            return {"skipped": "chunk_missing"}
        chunk = rows[0]
        content = (chunk.get("content") or "").strip()
        if not content:
            return {"skipped": "empty_content"}

        tag_list = tags or DEFAULT_RELATION_TAGS
        system = EXTRACT_SYSTEM.format(tags="、".join(tag_list))
        kwargs: dict[str, Any] = {"temperature": 0.3}
        if model_id:
            kwargs["model"] = model_id
        chat = create_chat_model(**kwargs)
        resp = chat.invoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=content[:6000]),
            ]
        )
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        graph = _parse_graph_json(text)
        for node in graph.nodes:
            node.chunks = [str(chunk_id)]

        self.graph.add_graph(
            NameSpace(knowledge_base=str(kb_id), document=document_id),
            [graph],
        )
        return {
            "chunk_id": chunk_id,
            "nodes": len(graph.nodes),
            "relations": len(graph.relations),
        }

    def enqueue_document_extract(
        self,
        *,
        document_id: str,
        kb_id: int,
        owner: str | None = None,
    ) -> int:
        """文档入库完成后，为每个文本 chunk 入队图谱抽取。返回入队数量。"""
        from config.settings import settings

        if not settings.neo4j_enable:
            return 0
        kb = self.store.get_knowledge_base(kb_id, owner=owner)
        if not kb or not kb.get("graph_enabled"):
            return 0
        if not self.graph.enabled:
            logger.warning("graph_enabled but Neo4j unavailable; skip extract for %s", document_id)
            return 0

        # 重新解析时先清旧图
        self.graph.del_graph(
            [NameSpace(knowledge_base=str(kb_id), document=document_id)]
        )

        chunk_ids = self.store.list_text_chunk_ids(document_id, owner=owner)
        model_id = kb.get("summary_model_id") or None
        from api.tasks import extract_chunk_graph_task

        enqueued = 0
        for cid in chunk_ids:
            extract_chunk_graph_task.delay(
                chunk_id=cid,
                kb_id=kb_id,
                document_id=document_id,
                model_id=model_id,
            )
            enqueued += 1
        logger.info(
            "enqueued %s graph extract tasks for document %s (kb=%s)",
            enqueued,
            document_id,
            kb_id,
        )
        return enqueued

    def delete_document_graph(self, kb_id: int | None, document_id: str) -> None:
        if kb_id is None:
            return
        self.graph.del_graph(
            [NameSpace(knowledge_base=str(kb_id), document=document_id)]
        )

    def delete_kb_graph(self, kb_id: int) -> None:
        self.graph.del_graph([NameSpace(knowledge_base=str(kb_id))])
