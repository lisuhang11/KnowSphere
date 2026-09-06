"""KnowSphere 对话图：编译智能推理 ReAct StateGraph。

唯一组图入口。langgraph.json 与 FastAPI / 评测均指向 build_agent。
agent / generate 同时挂 sync+async：评测与单测走 invoke，会话走 astream。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph._internal._runnable import RunnableCallable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import RetryPolicy

from agents.context import Context
from agents.nodes.agent import acall_agent, call_agent
from agents.nodes.generate import acall_generate, call_generate
from agents.nodes.manage_memory import manage_memory
from agents.nodes.prepare_context import prepare_context
from agents.nodes.query_understand import query_understand, route_after_understand
from agents.nodes.sources import collect_sources
from agents.state import InputState, KnowSphereState, OutputState
from config.settings import settings
from prompts import build_system_prompt
from tools import get_tools

_TOOLS_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0)


def _make_agent_runnable(
    system_prompt: str,
    tool_list: list[Any],
    chat_model_kwargs: dict[str, Any] | None = None,
) -> RunnableCallable:
    return RunnableCallable(
        partial(
            call_agent,
            system_prompt=system_prompt,
            tool_list=tool_list,
            chat_model_kwargs=chat_model_kwargs,
        ),
        partial(
            acall_agent,
            system_prompt=system_prompt,
            tool_list=tool_list,
            chat_model_kwargs=chat_model_kwargs,
        ),
        name="agent",
    )


def _make_generate_runnable(
    system_prompt: str,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> RunnableCallable:
    return RunnableCallable(
        partial(
            call_generate,
            system_prompt=system_prompt,
            chat_model_kwargs=chat_model_kwargs,
        ),
        partial(
            acall_generate,
            system_prompt=system_prompt,
            chat_model_kwargs=chat_model_kwargs,
        ),
        name="generate",
    )


def build_agent(
    checkpointer=None,
    *,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """构建并编译 KnowSphere 智能推理图。

    checkpointer: AsyncPostgresSaver / MemorySaver，启用多轮对话记忆。
    system_prompt: 覆盖默认系统提示（评测英文提示等场景）。
    tools: 覆盖默认工具列表（评测可只挂 doc_retrieval）。
    """
    prompt = system_prompt or build_system_prompt(settings.citation_enabled)
    tool_list = tools if tools is not None else get_tools()
    workflow = StateGraph(
        KnowSphereState,
        Context,
        input_schema=InputState,
        output_schema=OutputState,
    )
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("manage_memory", manage_memory)
    workflow.add_node("query_understand", query_understand)
    workflow.add_node("agent", _make_agent_runnable(prompt, tool_list, chat_model_kwargs))
    workflow.add_node("tools", ToolNode(tool_list), retry_policy=_TOOLS_RETRY)
    workflow.add_node("collect_sources", collect_sources)
    workflow.add_node("generate", _make_generate_runnable(prompt, chat_model_kwargs))

    workflow.add_edge(START, "prepare_context")
    workflow.add_edge("prepare_context", "manage_memory")
    workflow.add_edge("manage_memory", "query_understand")
    workflow.add_conditional_edges(
        "query_understand",
        route_after_understand,
        {
            "agent": "agent",
            "generate": "generate",
        },
    )
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    workflow.add_edge("tools", "collect_sources")
    workflow.add_edge("collect_sources", "agent")
    workflow.add_edge("generate", END)

    return workflow.compile(checkpointer=checkpointer, name="knowsphere_agent")
