"""StateGraph：智能推理 ReAct（agent ↔ tools），非工具意图走 generate。"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph._internal._runnable import RunnableCallable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import RetryPolicy

from agents.nodes.agent import acall_agent, call_agent
from agents.nodes.generate import acall_generate, call_generate
from agents.nodes.manage_memory import manage_memory
from agents.nodes.prepare_context import prepare_context
from agents.nodes.query_understand import query_understand, route_after_understand
from agents.nodes.sources import collect_sources
from states import AgentConfig, InputState, KnowSphereState, OutputState
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


def compile_workflow(
    *,
    system_prompt: str,
    tool_list: list[Any] | None = None,
    checkpointer=None,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """编译 KnowSphere 智能推理图：需要工具 → ReAct；否则一次生成。"""
    tools = tool_list if tool_list is not None else get_tools()
    workflow = StateGraph(
        KnowSphereState,
        AgentConfig,
        input_schema=InputState,
        output_schema=OutputState,
    )
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("manage_memory", manage_memory)
    workflow.add_node("query_understand", query_understand)
    workflow.add_node("agent", _make_agent_runnable(system_prompt, tools, chat_model_kwargs))
    workflow.add_node("tools", ToolNode(tools), retry_policy=_TOOLS_RETRY)
    workflow.add_node("collect_sources", collect_sources)
    workflow.add_node("generate", _make_generate_runnable(system_prompt, chat_model_kwargs))

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
