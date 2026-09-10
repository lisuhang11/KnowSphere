"""tools 节点：执行后按 L1 规则外置大结果。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.prebuilt.tool_node import ToolCallRequest

from utils.tool_result_store import offload_tool_message


def _config_from_request(request: ToolCallRequest) -> dict | None:
    runtime = getattr(request, "runtime", None)
    if runtime is None:
        return None
    return getattr(runtime, "config", None)


def _wrap_offload(request: ToolCallRequest, execute: Any) -> Any:
    result = execute(request)
    if isinstance(result, ToolMessage):
        return offload_tool_message(result, config=_config_from_request(request))
    return result


async def _awrap_offload(request: ToolCallRequest, execute: Any) -> Any:
    result = await execute(request)
    if isinstance(result, ToolMessage):
        return offload_tool_message(result, config=_config_from_request(request))
    return result


def make_tools_node(tool_list: list[Any]) -> ToolNode:
    return ToolNode(
        tool_list,
        wrap_tool_call=_wrap_offload,
        awrap_tool_call=_awrap_offload,
    )
