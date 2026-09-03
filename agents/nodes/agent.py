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
    has_doc = "doc_retrieval" in bound
    has_grep = "grep_chunks" in bound
    has_list = "list_chunks" in bound
    has_doc_info = "get_document_info" in bound
    parts = [system_prompt]
    web_label = "已开启" if web_on and has_web_tool else "未开启"
    if graph_on and has_graph_tool:
        graph_label = "已开启"
    elif kb_ids:
        graph_label = "未开启（所选库未开图谱，或 Neo4j 未启用）"
    else:
        graph_label = "未开启（未选择知识库）"
    parts.append(
        f"\n\n【本轮能力】联网搜索：{web_label}。知识图谱：{graph_label}。"
    )
    if kb_ids:
        seq: list[str] = []
        if has_doc:
            seq.append("库内事实必须先调用 doc_retrieval")
        if has_grep:
            seq.append("专名/编号/错误码用 grep_chunks")
        if has_list:
            seq.append("摘要不够再用 list_chunks，传入检索结果的 chunk_id / cN 或 document_id / dN 精读")
        if has_doc_info:
            seq.append("文件名和解析状态用 get_document_info（无正文）")
        if has_graph_tool:
            seq.append("关系型问题可在检索之后再 query_knowledge_graph（可选，不能替代语义检索）")
        if has_web_tool:
            seq.append("仅当库内缺失或不相关时才 web_search")
        seq_text = "；".join(seq) + "。" if seq else ""
        parts.append(
            "\n\n【本轮已限定知识库】"
            f"{seq_text}"
            "禁止用互联网公开常识（同名公众人物等）顶替用户文档。"
            "检索无相关内容时明确说明未找到。"
        )
    else:
        web_hint = (
            "实时/公开信息可使用 web_search / web_fetch。"
            if has_web_tool
            else "本轮未开启联网搜索。"
        )
        parts.append(
            "\n\n【本轮未选择知识库】无法检索用户文档。"
            "对知识库中的人物、项目等问题，须提示用户在输入框上方选择知识库。"
            f"{web_hint}"
        )
    rewrite = (rewrite_query or "").strip()
    if rewrite:
        parts.append(
            f"\n\n【本轮改写检索词】{rewrite}\n"
            "调用 doc_retrieval / grep_chunks / web_search 时优先使用该检索词；"
            "多跳可按中间结果改写后再搜；正文不够用 list_chunks 精读。"
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
