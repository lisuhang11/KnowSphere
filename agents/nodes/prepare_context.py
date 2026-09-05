"""prepare_context：整理当前 query 与会话历史，供 query_understand 使用。"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from config.settings import settings
from schemas.query import fallback_intent
from states import KnowSphereState
from utils.agent_runtime import resolve_agent_tool_names
from utils.language import ANSWER_LANGUAGE_EN, answer_language_for_query
from utils.message_content import (
    message_has_attachments,
    message_has_images,
)
from utils.run_config import (
    graph_enabled_from_config,
    kb_ids_from_config,
    web_search_enabled_from_config,
)
from utils.short_term_memory import extract_history_pairs_from_messages


def _is_human(msg: BaseMessage) -> bool:
    if isinstance(msg, HumanMessage):
        return True
    role = getattr(msg, "type", None)
    return role in ("human", "user")


def extract_history_pairs(messages: list[BaseMessage], max_rounds: int) -> tuple[str | None, list[dict[str, str]]]:
    """返回 (current_query, history_pairs)。history 不含当前轮。"""
    return extract_history_pairs_from_messages(messages, max_rounds)


def _empty_turn(
    *,
    kb_selected: bool = False,
    web_search_enabled: bool = False,
    graph_enabled: bool = False,
    agent_has_tools: bool = False,
) -> dict:
    """清零单轮临时通道，避免 checkpoint 脏读上一轮 intent / 图片描述等。"""
    return {
        "current_query": "",
        "rewrite_query": "",
        "answer_language": ANSWER_LANGUAGE_EN,
        "intent": "",
        "history_pairs": [],
        "kb_selected": kb_selected,
        "web_search_enabled": web_search_enabled,
        "graph_enabled": graph_enabled,
        "system_prompt_override": "",
        "has_images": False,
        "has_attachments": False,
        "image_description": "",
        "last_sources": [],
        "context_block": "",
        "retrieval_note": "",
        "agent_has_tools": agent_has_tools,
        "asker_background": "",
    }


def prepare_context(state: KnowSphereState, config: RunnableConfig) -> dict:
    messages = list(state.get("messages") or [])
    current_query, history_pairs = extract_history_pairs_from_messages(
        messages, settings.stm_keep_turns
    )
    kb_selected = bool(kb_ids_from_config(config))
    web_on = web_search_enabled_from_config(config)
    graph_on = graph_enabled_from_config(config)
    allowed = resolve_agent_tool_names(config)
    agent_has_tools = bool(allowed)

    if not current_query:
        return _empty_turn(
            kb_selected=kb_selected,
            web_search_enabled=web_on,
            graph_enabled=graph_on,
            agent_has_tools=agent_has_tools,
        )

    has_images = False
    has_attachments = False
    human_indices = [i for i, m in enumerate(messages) if _is_human(m)]
    if human_indices:
        last_human = messages[human_indices[-1]]
        has_images = message_has_images(last_human)
        has_attachments = message_has_attachments(last_human)

    default_intent = fallback_intent(
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
    )
    return {
        **_empty_turn(
            kb_selected=kb_selected,
            web_search_enabled=web_on,
            graph_enabled=graph_on,
            agent_has_tools=agent_has_tools,
        ),
        "current_query": current_query,
        "history_pairs": history_pairs,
        "rewrite_query": current_query,
        "answer_language": answer_language_for_query(current_query),
        "intent": default_intent,
        "has_images": has_images,
        "has_attachments": has_attachments,
    }
