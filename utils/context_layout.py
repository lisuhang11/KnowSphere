"""主对话请求布局：稳定前缀、任务配置、交接摘要、最近 N 轮、本轮动态消息。

摘要和动态消息只出现在发给模型的视图里，不写入 checkpoint。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from utils.language import ANSWER_LANGUAGE_EN, normalize_answer_language
from utils.run_config import (
    graph_enabled_from_config,
    kb_ids_from_config,
    pinned_skill_names_from_config,
    web_search_enabled_from_config,
)

_STORED_NOTE = (
    "### Stored tool results\n"
    "A tool message may be a reference: "
    '{"__stored":true,"__refId":"...","__summary":"..."}. '
    "Use __summary when it is enough. "
    "If you need the original payload, call get_stored_data with that __refId. "
    "Do not invent ref ids or copy truncated arrays from memory."
)

_VIEW_HANDOVER = "handover"
_VIEW_TURN = "turn_context"


def stabilize_system_prompt(prompt: str) -> str:
    """系统级正文不代入本轮开关和语言，避免前缀每轮变化。"""
    text = prompt or ""
    text = text.replace("{{web_search_status}}", "the status in Task configuration")
    text = text.replace("{{language}}", "the language in Task configuration")
    return text.rstrip()


def render_system_content(
    prompt: str,
    config: RunnableConfig | None,
    *,
    bound_tool_names: list[str] | None = None,
    answer_language: str | None = None,
) -> str:
    """系统级正文在前，任务级配置接在后面，同属一条系统消息。"""
    stable = stabilize_system_prompt(prompt)
    bound = set(bound_tool_names or [])
    if "get_stored_data" in bound:
        stable = f"{stable}\n\n{_STORED_NOTE}" if stable else _STORED_NOTE
    task = _task_configuration(
        config,
        bound_tool_names=bound,
        answer_language=answer_language,
    )
    if not stable:
        return task
    return f"{stable}\n\n{task}"


def _task_configuration(
    config: RunnableConfig | None,
    *,
    bound_tool_names: set[str],
    answer_language: str | None,
) -> str:
    kb_ids = kb_ids_from_config(config)
    web_on = web_search_enabled_from_config(config)
    graph_on = graph_enabled_from_config(config)
    has_web_tool = "web_search" in bound_tool_names or "web_fetch" in bound_tool_names
    has_graph_tool = "query_knowledge_graph" in bound_tool_names
    web_label = "Enabled" if web_on and has_web_tool else "Disabled"
    graph_label = "Enabled" if graph_on and has_graph_tool else "Disabled"
    language = normalize_answer_language(answer_language or ANSWER_LANGUAGE_EN)
    lines = [
        "### Task configuration",
        f"Web Search: {web_label}",
        f"Knowledge Graph: {graph_label}",
        f"User Language: {language}",
        f"ALWAYS respond in {language}",
    ]
    if kb_ids:
        lines.append(
            "Bound knowledge bases are selected for this turn. "
            "Search them with the tools in your list."
        )
    elif has_web_tool:
        lines.append(
            "No knowledge base is selected this turn. If the question depends on "
            "uploaded documents, tell the user to select a knowledge base. "
            "Web search / web_fetch may be used if enabled."
        )
    else:
        lines.append(
            "No knowledge base is selected this turn. If the question depends on "
            "uploaded documents, tell the user to select a knowledge base. "
            "Web search is not enabled this turn."
        )
    return "\n".join(lines)


def handover_message(summary: str | None) -> HumanMessage | None:
    text = (summary or "").strip()
    if not text:
        return None
    return HumanMessage(content=text, additional_kwargs={"ks_view": _VIEW_HANDOVER})


def turn_context_message(
    *,
    rewrite_query: str | None = None,
    image_description: str | None = None,
    pinned_skills: list[str] | None = None,
    asker_background: str | None = None,
) -> HumanMessage | None:
    """本轮才变的内容。不进 checkpoint。"""
    parts: list[str] = []
    rewrite = (rewrite_query or "").strip()
    if rewrite:
        parts.append(
            f"Rewritten query for this turn: {rewrite}\n"
            "Prefer this query for doc_retrieval / grep_chunks / web_search; "
            "rewrite again from intermediate results on multi-hop tasks."
        )
    image = (image_description or "").strip()
    if image:
        parts.append(f"[用户上传图片内容]\n{image}")
    pinned = [name.strip() for name in (pinned_skills or []) if name and name.strip()]
    if pinned:
        listed = "\n".join(f"- /skills/{name}/SKILL.md" for name in pinned)
        parts.append(f"本轮点名技能，回答前先 read_file：\n{listed}")
    asker = (asker_background or "").strip()
    if asker:
        parts.append(asker)
    if not parts:
        return None
    return HumanMessage(
        content="【本轮上下文】\n" + "\n\n".join(parts),
        additional_kwargs={"ks_view": _VIEW_TURN},
    )


def llm_history_messages(state: dict[str, Any]) -> list[BaseMessage]:
    """最近 N 轮原文加本轮，不改写检索结果，也不把摘要塞进这些消息。"""
    from config.settings import settings
    from utils.short_term_memory import build_memory_view

    return build_memory_view(
        list(state.get("messages") or []),
        session_summary=str(state.get("session_summary") or ""),
        summary_upto_id=str(state.get("summary_upto_message_id") or ""),
        max_context_tokens=settings.stm_max_context_tokens,
        keep_turns=settings.stm_keep_turns,
        consolidate_ratio=settings.stm_consolidate_ratio,
        hard_trim_ratio=settings.stm_hard_trim_ratio,
        redact_old_retrieval=False,
        compact_human=False,
        prompt_tokens=int(state.get("last_prompt_tokens") or 0),
        compact_trigger_ratio=settings.stm_compact_trigger_ratio,
        compact_target_ratio=settings.stm_compact_target_ratio,
    ).window_messages


def assemble_model_messages(
    prompt: str,
    messages: list[BaseMessage],
    config: RunnableConfig | None,
    *,
    bound_tool_names: list[str] | None = None,
    answer_language: str | None = None,
    session_summary: str | None = None,
    rewrite_query: str | None = None,
    image_description: str | None = None,
    pinned_skills: list[str] | None = None,
    asker_background: str | None = None,
    context_block: str | None = None,
) -> list[BaseMessage]:
    """系统提示、交接摘要、历史、动态消息、本轮。后两段视图消息不在 messages 入参里。"""
    system = SystemMessage(
        content=render_system_content(
            prompt,
            config,
            bound_tool_names=bound_tool_names,
            answer_language=answer_language,
        )
    )
    prior, current = _split_current_turn(messages)
    current = _append_context_block(current, context_block or "")
    out: list[BaseMessage] = [system]
    handover = handover_message(session_summary)
    if handover is not None:
        out.append(handover)
    out.extend(prior)
    dynamic = turn_context_message(
        rewrite_query=rewrite_query,
        image_description=image_description,
        pinned_skills=pinned_skills
        if pinned_skills is not None
        else pinned_skill_names_from_config(config),
        asker_background=asker_background,
    )
    if dynamic is not None:
        out.append(dynamic)
    out.extend(current)
    return out


def _split_current_turn(
    messages: list[BaseMessage],
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    last_human: int | None = None
    for index, msg in enumerate(messages):
        if isinstance(msg, HumanMessage):
            last_human = index
    if last_human is None:
        return list(messages), []
    return list(messages[:last_human]), list(messages[last_human:])


def _append_context_block(messages: list[BaseMessage], context_block: str) -> list[BaseMessage]:
    block = (context_block or "").strip()
    if not block or not messages:
        return messages
    out = list(messages)
    first = out[0]
    if not isinstance(first, HumanMessage):
        return messages
    text = first.content if isinstance(first.content, str) else str(first.content)
    if "【知识库检索结果】" in text:
        return out
    new_msg = HumanMessage(content=f"{text.rstrip()}\n\n{block}".strip())
    kwargs = dict(getattr(first, "additional_kwargs", None) or {})
    if kwargs:
        new_msg.additional_kwargs = kwargs
    out[0] = new_msg
    return out
