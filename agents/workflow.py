"""StateGraph 工作流：agent ↔ tools ↔ collect_sources 显式编排。

状态分层见 states：InputState / OverallState / OutputState + AgentConfig(context_schema)。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph._internal._runnable import RunnableCallable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import RetryPolicy

from agents.nodes.agent import acall_agent, call_agent
from agents.nodes.prepare_context import prepare_context
from agents.nodes.query_understand import query_understand, route_after_understand
from agents.nodes.retrieve import prefetch_retrieval
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

def compile_workflow(
    *,
    system_prompt: str,
    tool_list: list[Any] | None = None,
    checkpointer=None,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """构建并编译 KnowSphere StateGraph（ReAct 循环 + 检索来源汇总节点）。"""
    tools = tool_list if tool_list is not None else get_tools()
    tool_node = ToolNode(tools)

    workflow = StateGraph(
        KnowSphereState,
        AgentConfig,
        input_schema=InputState,
        output_schema=OutputState,
    )
    workflow.add_node("prepare_context", prepare_context)
    workflow.add_node("query_understand", query_understand)
    workflow.add_node("prefetch_retrieval", prefetch_retrieval)
    workflow.add_node("agent", _make_agent_runnable(system_prompt, tools, chat_model_kwargs))
    workflow.add_node("tools", tool_node, retry_policy=_TOOLS_RETRY)
    workflow.add_node("collect_sources", collect_sources)

    workflow.add_edge(START, "prepare_context")
    workflow.add_edge("prepare_context", "query_understand")
    workflow.add_conditional_edges(
        "query_understand",
        route_after_understand,
        {
            "prefetch_retrieval": "prefetch_retrieval",
            "agent": "agent",
        },
    )
    workflow.add_edge("prefetch_retrieval", "agent")
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", "__end__": END},
    )
    workflow.add_edge("tools", "collect_sources")
    workflow.add_edge("collect_sources", "agent")

    return workflow.compile(checkpointer=checkpointer, name="knowsphere_agent")
