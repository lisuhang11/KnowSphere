"""manage_memory：短期滚动摘要 + 长期记忆召回。在 query_understand 之前运行。"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from config.settings import settings
from models import create_chat_model
from states import KnowSphereState
from tools.events import emit_thinking
from utils.long_term_memory import (
    format_asker_background,
    remember_explicit,
    retrieval_context_for,
)
from utils.message_content import message_text
from utils.run_config import chat_model_kwargs_from_config, thread_id_from_config
from utils.short_term_memory import (
    SUMMARY_SYSTEM_PROMPT,
    build_memory_view,
    build_summary_user_prompt,
    extract_working_memory,
    fallback_archive_summary,
    format_archive_for_summary,
)

logger = logging.getLogger(__name__)

_LLM_KWARGS: dict = {
    "temperature": 0.3,
    "max_tokens": 800,
    "extra_body": {"enable_thinking": False},
}


def _view_kwargs() -> dict:
    return {
        "max_context_tokens": settings.stm_max_context_tokens,
        "keep_turns": settings.stm_keep_turns,
        "consolidate_ratio": settings.stm_consolidate_ratio,
        "hard_trim_ratio": settings.stm_hard_trim_ratio,
        "redact_old_retrieval": settings.stm_redact_old_retrieval,
    }


def _summarize_archive(archive_text: str, previous_summary: str, config: RunnableConfig) -> str:
    llm = create_chat_model(**chat_model_kwargs_from_config(config, _LLM_KWARGS))
    resp = llm.invoke(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": build_summary_user_prompt(archive_text, previous_summary)},
        ],
        config=config,
    )
    text = message_text(getattr(resp, "content", ""))
    if not text:
        raise ValueError("empty summary")
    return text


def manage_memory(state: KnowSphereState, config: RunnableConfig) -> dict:
    messages = list(state.get("messages") or [])
    previous_summary = str(state.get("session_summary") or "")
    summary_upto = str(state.get("summary_upto_message_id") or "")
    view = build_memory_view(
        messages,
        session_summary=previous_summary,
        summary_upto_id=summary_upto,
        **_view_kwargs(),
    )
    updates: dict = {
        "working_memory": extract_working_memory(messages),
        "history_pairs": view.history_pairs,
    }
    current_query = str(state.get("current_query") or "").strip()
    session_id = thread_id_from_config(config) or ""
    if current_query:
        remembered = remember_explicit(
            current_query, config=config, session_id=session_id
        )
        if remembered:
            emit_thinking("已记下跨会话记忆，供后续改写与意图识别使用。")
    asker = format_asker_background(retrieval_context_for(config=config))
    updates["asker_background"] = asker

    if not view.needs_consolidation or not view.archive_messages:
        return updates

    archive_text = format_archive_for_summary(view.archive_messages)
    if not archive_text:
        return updates

    emit_thinking("正在整理更早的对话记忆…")
    try:
        summary = _summarize_archive(archive_text, previous_summary, config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话摘要失败，改用截断归档: %s", exc)
        summary = fallback_archive_summary(archive_text, previous_summary)

    updates["session_summary"] = summary
    if view.archive_end_id:
        updates["summary_upto_message_id"] = view.archive_end_id
    return updates
