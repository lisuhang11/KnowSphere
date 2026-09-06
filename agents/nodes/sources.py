"""collect_sources 节点：从 tool 消息汇总本轮检索来源，写入 state.last_sources。"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from agents.state import KnowSphereState
from utils.long_term_memory import record_answer_sources
from utils.short_term_memory import turn_ranges

logger = logging.getLogger(__name__)


def collect_sources(state: KnowSphereState, config: RunnableConfig = None) -> dict[str, list[dict]]:
    """解析本轮检索类 ToolMessage，合并为 last_sources。

    只扫当前轮（最后一条 Human 起），避免把历史检索来源挂到本轮回答的 ks_citations。
    """
    messages = list(state.get("messages") or [])
    ranges = turn_ranges(messages)
    if not ranges:
        return {"last_sources": []}
    start, end = ranges[-1]
    sources: list[dict] = []
    for msg in messages[start:end]:
        if not isinstance(msg, ToolMessage) or msg.name not in (
            "doc_retrieval",
            "grep_chunks",
            "list_chunks",
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
    if sources and config is not None:
        record_answer_sources(sources, config=config)
    return {"last_sources": sources}
