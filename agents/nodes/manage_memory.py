"""manage_memory：短期滚动摘要 + 长期记忆召回。在 query_understand 之前运行。"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig

from agents.state import KnowSphereState
from config.settings import settings
from tools.events import emit_thinking
from utils.conversation_compaction import compact_archive_to_handover, first_user_request
from utils.long_term_memory import (
    format_asker_background,
    remember_explicit,
    retrieval_context_for,
)
from utils.run_config import thread_id_from_config
from utils.short_term_memory import (
    build_memory_view,
    extract_working_memory,
)

logger = logging.getLogger(__name__)


def _view_kwargs(state: KnowSphereState) -> dict:
    return {
        "max_context_tokens": settings.stm_max_context_tokens,
        "keep_turns": settings.stm_keep_turns,
        "consolidate_ratio": settings.stm_consolidate_ratio,
        "hard_trim_ratio": settings.stm_hard_trim_ratio,
        "redact_old_retrieval": settings.stm_redact_old_retrieval,
        "prompt_tokens": int(state.get("last_prompt_tokens") or 0),
        "compact_trigger_ratio": settings.stm_compact_trigger_ratio,
        "compact_target_ratio": settings.stm_compact_target_ratio,
    }


def manage_memory(state: KnowSphereState, config: RunnableConfig) -> dict:
    messages = list(state.get("messages") or [])
    previous_summary = str(state.get("session_summary") or "")
    summary_upto = str(state.get("summary_upto_message_id") or "")
    view = build_memory_view(
        messages,
        session_summary=previous_summary,
        summary_upto_id=summary_upto,
        **_view_kwargs(state),
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

    emit_thinking("正在整理交接文档…")
    try:
        summary = compact_archive_to_handover(
            view.archive_messages,
            previous_summary,
            config=config,
            original_hint=first_user_request(messages),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("会话交接失败: %s", exc)
        summary = previous_summary
    if not summary:
        return updates

    updates["session_summary"] = summary
    if view.archive_end_id:
        updates["summary_upto_message_id"] = view.archive_end_id
    return updates
