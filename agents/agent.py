"""对话图组装：编译智能推理 ReAct StateGraph。"""

from __future__ import annotations

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from agents.workflow import compile_workflow
from config.settings import settings
from prompts import build_system_prompt
from tools import get_tools


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
    return compile_workflow(
        system_prompt=prompt,
        tool_list=tool_list,
        checkpointer=checkpointer,
        chat_model_kwargs=chat_model_kwargs,
    )
