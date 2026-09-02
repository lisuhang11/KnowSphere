"""collect_sources 节点：从 tool 消息汇总本轮检索来源，写入 state.last_sources。"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import ToolMessage

from states import KnowSphereState

logger = logging.getLogger(__name__)


def collect_sources(state: KnowSphereState) -> dict[str, list[dict]]:
    """解析检索类 ToolMessage，合并为 last_sources（供观测与后续节点扩展）。"""
    sources: list[dict] = []
    for msg in state["messages"]:
        if not isinstance(msg, ToolMessage) or msg.name not in (
            "doc_retrieval",
            "query_knowledge_graph",
            "web_search",
            "web_fetch",
        ):
            continue
        try:
            payload = json.loads(msg.content)
            for item in payload.get("sources") or []:
                if isinstance(item, dict):
                    sources.append(item)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("跳过无法解析的检索工具消息: %s", exc)
    return {"last_sources": sources}
