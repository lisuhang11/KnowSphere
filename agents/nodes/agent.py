"""agent 节点：绑定工具后调用主模型（ReAct 的 think 步）。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from agents.state import KnowSphereState
from models import create_chat_model
from skills.catalog import any_skill_has_scripts
from tools.catalog import get_tool_spec
from tools.events import emit_thinking
from tools.skills import SKILL_RUNTIME_TOOL_NAMES
from tools.storage import STORAGE_RUNTIME_TOOL_NAMES
from utils.agent_runtime import (
    resolve_agent_skill_names,
    resolve_agent_tool_names,
    resolve_system_prompt,
)
from utils.citation import citation_payload_from_source_dicts
from utils.context_layout import assemble_model_messages, llm_history_messages
from utils.conversation_compaction import maybe_compact_state, prompt_tokens_from_response
from utils.language import ANSWER_LANGUAGE_EN, answer_language_from_state
from utils.run_config import (
    chat_model_kwargs_from_config,
    graph_enabled_from_config,
    kb_ids_from_config,
    web_search_enabled_from_config,
)


def _are_more_steps_needed(state: KnowSphereState, response: AIMessage) -> bool:
    if not response.tool_calls:
        return False
    remaining = state.get("remaining_steps")
    if remaining is not None and remaining < 2:
        return True
    return False


def tools_for_state(
    config: RunnableConfig | None,
    tool_list: list[Any],
    state: KnowSphereState | None = None,
) -> list[Any]:
    """问候/附件等覆盖提示时不绑工具；否则按智能体绑定的工具 + 知识库/联网开关裁剪。"""
    if state and (state.get("system_prompt_override") or "").strip():
        return []
    allowed = resolve_agent_tool_names(config)
    skill_names = resolve_agent_skill_names(config)
    skill_enabled = bool(skill_names)
    offer_execute = skill_enabled and any_skill_has_scripts(skill_names)
    kb_ids = kb_ids_from_config(config)
    selected: list[Any] = []
    seen: set[str] = set()
    for tool in tool_list:
        name = getattr(tool, "name", None)
        if not name or name in seen:
            continue
        if name in SKILL_RUNTIME_TOOL_NAMES:
            if name == "execute_skill_script" and not offer_execute:
                continue
            if skill_enabled:
                seen.add(name)
                selected.append(tool)
            continue
        if name in STORAGE_RUNTIME_TOOL_NAMES:
            seen.add(name)
            selected.append(tool)
            continue
        if allowed is not None and name not in allowed:
            continue
        spec = get_tool_spec(name)
        if spec is not None:
            if spec.requires_kb and not kb_ids:
                continue
            if spec.requires_web and not web_search_enabled_from_config(config):
                continue
            if spec.requires_graph and not graph_enabled_from_config(config):
                continue
        seen.add(name)
        selected.append(tool)
    return selected


def _prepare_messages(
    system_prompt: str,
    messages: list[BaseMessage],
    config: RunnableConfig | None,
    *,
    rewrite_query: str | None = None,
    bound_tool_names: list[str] | None = None,
    memory_suffix: str | None = None,
    answer_language: str | None = None,
    session_summary: str | None = None,
    image_description: str | None = None,
    asker_background: str | None = None,
    pinned_skills: list[str] | None = None,
) -> list[BaseMessage]:
    """memory_suffix 已不再写入系统提示，保留参数以免旧调用方报错。"""
    del memory_suffix
    return assemble_model_messages(
        system_prompt,
        messages,
        config,
        bound_tool_names=bound_tool_names,
        answer_language=answer_language,
        session_summary=session_summary,
        rewrite_query=rewrite_query,
        image_description=image_description,
        pinned_skills=pinned_skills,
        asker_background=asker_background,
    )


def _llm_messages(
    state: KnowSphereState,
    config: RunnableConfig,
    system_prompt: str,
    bound_tool_names: list[str] | None = None,
) -> list[BaseMessage]:
    return _prepare_messages(
        system_prompt,
        llm_history_messages(state),
        config,
        rewrite_query=state.get("rewrite_query"),
        bound_tool_names=bound_tool_names,
        answer_language=answer_language_from_state(state),
        session_summary=str(state.get("session_summary") or ""),
        image_description=str(state.get("image_description") or ""),
        asker_background=str(state.get("asker_background") or ""),
    )


def _step_limit_message(language: str) -> str:
    if language == ANSWER_LANGUAGE_EN:
        return "Sorry, this request needs more steps. Please simplify or split the question and try again."
    return "抱歉，处理该请求需要更多步骤，请简化问题或拆分后再试。"


def _finalize_response(state: KnowSphereState, response: Any) -> AIMessage:
    if not isinstance(response, AIMessage):
        response = AIMessage(
            content=getattr(response, "content", None) or str(response),
            tool_calls=list(getattr(response, "tool_calls", None) or []),
            id=getattr(response, "id", None),
        )
    if _are_more_steps_needed(state, response):
        return AIMessage(
            id=response.id,
            content=_step_limit_message(answer_language_from_state(state)),
        )
    if not getattr(response, "tool_calls", None):
        cites = citation_payload_from_source_dicts(state.get("last_sources") or [])
        if cites:
            kwargs = dict(getattr(response, "additional_kwargs", None) or {})
            kwargs["ks_citations"] = cites
            response.additional_kwargs = kwargs
    return response


def call_agent(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    tool_list: list[Any],
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = tools_for_state(config, tool_list, state)
    bound_names = [str(getattr(t, "name", "") or "") for t in tools]
    prompt = resolve_system_prompt(config, system_prompt, bound_tool_names=bound_names)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    if tools:
        model = model.bind_tools(tools)
    compact = maybe_compact_state(state, config)
    if compact:
        state = {**state, **compact}
    messages = _llm_messages(state, config, prompt, bound_names)
    emit_thinking("正在思考如何作答…")
    response = model.invoke(messages, config)
    updates: dict[str, Any] = {**compact, "messages": [_finalize_response(state, response)]}
    used = prompt_tokens_from_response(response)
    if used:
        updates["last_prompt_tokens"] = used
    return updates


async def acall_agent(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    tool_list: list[Any],
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = tools_for_state(config, tool_list, state)
    bound_names = [str(getattr(t, "name", "") or "") for t in tools]
    prompt = resolve_system_prompt(config, system_prompt, bound_tool_names=bound_names)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    if tools:
        model = model.bind_tools(tools)
    compact = maybe_compact_state(state, config)
    if compact:
        state = {**state, **compact}
    messages = _llm_messages(state, config, prompt, bound_names)
    emit_thinking("正在思考如何作答…")
    acc: Any = None
    async for chunk in model.astream(messages, config):
        acc = chunk if acc is None else acc + chunk
    if acc is None:
        acc = await model.ainvoke(messages, config)
    updates: dict[str, Any] = {**compact, "messages": [_finalize_response(state, acc)]}
    used = prompt_tokens_from_response(acc)
    if used:
        updates["last_prompt_tokens"] = used
    return updates
