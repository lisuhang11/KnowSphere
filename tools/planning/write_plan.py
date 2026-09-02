"""write_plan：多步任务先列出计划，再按步骤调其它工具。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from tools.events import emit_thinking, emit_tool_call, emit_tool_result


@tool
def write_plan(
    goal: str,
    steps: list[str],
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
) -> str:
    """写下或更新本轮任务计划。多跳、需组合知识库与联网、或超过两步的问题应先调用。

    列出具体步骤（检索什么、是否联网、如何综合），然后再逐步调用其它工具。
    简单单跳事实题不必调用。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    cleaned = [str(s).strip() for s in (steps or []) if str(s).strip()]
    goal_text = (goal or "").strip() or "完成本轮问题"
    lines = [f"目标：{goal_text}"]
    if cleaned:
        lines.append("步骤：")
        lines.extend(f"{i}. {s}" for i, s in enumerate(cleaned, 1))
    else:
        lines.append("步骤：尚未拆分")
    text = "\n".join(lines)
    emit_tool_call("write_plan", goal_text, writer)
    emit_thinking(f"【规划】\n{text}", writer)
    emit_tool_result("write_plan", f"已记录 {len(cleaned)} 步计划", writer=writer)
    return text
