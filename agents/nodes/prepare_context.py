"""prepare_context：整理当前 query 与会话历史，供 query_understand 使用。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from config.settings import settings
from states import KnowSphereState
from utils.message_content import (
    message_has_attachments,
    message_has_images,
    message_query_text,
    message_text,
)
from utils.run_config import kb_ids_from_config


def _message_text(msg: BaseMessage) -> str:
    return message_text(getattr(msg, "content", ""))


def _is_human(msg: BaseMessage) -> bool:
    if isinstance(msg, HumanMessage):
        return True
    role = getattr(msg, "type", None)
    return role in ("human", "user")


def _is_ai(msg: BaseMessage) -> bool:
    if isinstance(msg, AIMessage):
        return True
    role = getattr(msg, "type", None)
    return role in ("ai", "assistant")


def _is_retrieval_tool(msg: BaseMessage) -> bool:
    return isinstance(msg, ToolMessage) and getattr(msg, "name", None) == "doc_retrieval"


def extract_history_pairs(messages: list[BaseMessage], max_rounds: int) -> tuple[str | None, list[dict[str, str]]]:
    """返回 (current_query, history_pairs)。history 不含当前轮。"""
    human_indices = [
        i for i, m in enumerate(messages) if _is_human(m) and _message_text(m)
    ]
    if not human_indices:
        return None, []

    last_idx = human_indices[-1]
    last_msg = messages[last_idx]
    current_query = message_query_text(last_msg) or _message_text(last_msg)

    pairs: list[dict[str, str]] = []
    for hi in human_indices[:-1]:
        q = message_query_text(messages[hi]) or _message_text(messages[hi])
        answer = ""
        for j in range(hi + 1, len(messages)):
            if _is_human(messages[j]):
                break
            if _is_retrieval_tool(messages[j]):
                continue
            if _is_ai(messages[j]):
                answer = _message_text(messages[j])
                break
        if q:
            pairs.append({"query": q, "answer": answer})

    if max_rounds > 0:
        pairs = pairs[-max_rounds:]
    return current_query, pairs


def prepare_context(state: KnowSphereState, config: RunnableConfig) -> dict:
    messages = list(state.get("messages") or [])
    current_query, history_pairs = extract_history_pairs(
        messages, settings.max_rewrite_rounds
    )
    kb_selected = bool(kb_ids_from_config(config))

    if not current_query:
        return {}

    has_images = False
    has_attachments = False
    human_indices = [i for i, m in enumerate(messages) if _is_human(m)]
    if human_indices:
        last_human = messages[human_indices[-1]]
        has_images = message_has_images(last_human)
        has_attachments = message_has_attachments(last_human)

    default_intent = "kb_search" if kb_selected else "no_kb"
    return {
        "current_query": current_query,
        "history_pairs": history_pairs,
        "kb_selected": kb_selected,
        "rewrite_query": current_query,
        "intent": default_intent,
        "has_images": has_images,
        "has_attachments": has_attachments,
    }
