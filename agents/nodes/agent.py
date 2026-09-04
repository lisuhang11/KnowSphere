"""agent 节点：绑定工具后调用主模型（ReAct 的 think 步）。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agents.nodes.generate import _inject_image_description
from models import create_chat_model
from skills.catalog import any_skill_has_scripts
from states import KnowSphereState
from tools.catalog import get_tool_spec
from tools.events import emit_thinking
from tools.skills import SKILL_RUNTIME_TOOL_NAMES
from utils.agent_runtime import (
    resolve_agent_skill_names,
    resolve_agent_tool_names,
    resolve_system_prompt,
)
from utils.citation import citation_payload_from_source_dicts
from utils.run_config import (
    chat_model_kwargs_from_config,
    graph_enabled_from_config,
    kb_ids_from_config,
    web_search_enabled_from_config,
)
from utils.short_term_memory import memory_system_suffix_from_state, memory_view_from_state


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
) -> list[BaseMessage]:
    kb_ids = kb_ids_from_config(config)
    web_on = web_search_enabled_from_config(config)
    graph_on = graph_enabled_from_config(config)
    bound = set(bound_tool_names or [])
    has_web_tool = "web_search" in bound or "web_fetch" in bound
    has_graph_tool = "query_knowledge_graph" in bound
    web_label = "Enabled" if web_on and has_web_tool else "Disabled"
    graph_label = "Enabled" if graph_on and has_graph_tool else "Disabled"
    filled = system_prompt.replace("{{web_search_status}}", web_label)
    parts = [filled]
    parts.append(
        f"\n\n### System Status\nWeb Search: {web_label}\nKnowledge Graph: {graph_label}\nUser Language: 中文"
    )
    if kb_ids:
        parts.append("\n\nBound knowledge bases are selected for this turn. Search them with the tools in your list.")
    else:
        parts.append(
            "\n\nNo knowledge base is selected this turn. If the question depends on uploaded documents, tell the user to select a knowledge base. "
            + (
                "Web search / web_fetch may be used if enabled."
                if has_web_tool
                else "Web search is not enabled this turn."
            )
        )
    rewrite = (rewrite_query or "").strip()
    if rewrite:
        parts.append(
            f"\n\nRewritten query for this turn: {rewrite}\n"
            "Prefer this query for doc_retrieval / grep_chunks / web_search; rewrite again from intermediate results on multi-hop tasks."
        )
    memory_block = (memory_suffix or "").strip()
    if memory_block:
        parts.append("\n\n" + memory_block)
    return [SystemMessage(content="".join(parts))] + list(messages)


def _llm_messages(
    state: KnowSphereState,
    config: RunnableConfig,
    system_prompt: str,
    bound_tool_names: list[str] | None = None,
) -> list[BaseMessage]:
    window = memory_view_from_state(state).window_messages
    messages = _inject_image_description(
        window,
        str(state.get("image_description") or ""),
    )
    return _prepare_messages(
        system_prompt,
        messages,
        config,
        rewrite_query=state.get("rewrite_query"),
        bound_tool_names=bound_tool_names,
        memory_suffix=memory_system_suffix_from_state(state),
    )


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
            content="抱歉，处理该请求需要更多步骤，请简化问题或拆分后再试。",
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
    messages = _llm_messages(state, config, prompt, bound_names)
    emit_thinking("正在思考如何作答…")
    response = model.invoke(messages, config)
    return {"messages": [_finalize_response(state, response)]}


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
    messages = _llm_messages(state, config, prompt, bound_names)
    emit_thinking("正在思考如何作答…")
    acc: Any = None
    async for chunk in model.astream(messages, config):
        acc = chunk if acc is None else acc + chunk
    if acc is None:
        acc = await model.ainvoke(messages, config)
    return {"messages": [_finalize_response(state, acc)]}
