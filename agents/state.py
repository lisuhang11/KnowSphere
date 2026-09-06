"""图状态 schema（对齐 LangGraph input / overall / output）。

- InputState：invoke 入参契约（仅 messages）
- OutputState：invoke 对外返回（messages + last_sources）
- TurnState：单轮临时通道（prepare_context 入口重置，不加 reducer）
- OverallState / KnowSphereState：节点共享的完整内部状态
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from langgraph.managed import RemainingSteps


class InputState(TypedDict):
    """图调用输入：仅追加本轮消息。"""

    messages: Annotated[Sequence[BaseMessage], add_messages]


class OutputState(TypedDict):
    """图调用输出：对话消息 + 可选检索来源汇总。"""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    last_sources: NotRequired[list[dict]]


class TurnState(TypedDict, total=False):
    """单轮临时通道；由 prepare_context 每轮入口整体重置。"""

    current_query: str
    rewrite_query: str
    answer_language: str
    intent: str
    history_pairs: list[dict[str, str]]
    kb_selected: bool
    web_search_enabled: bool
    graph_enabled: bool
    system_prompt_override: str
    has_images: bool
    has_attachments: bool
    image_description: str
    context_block: str
    retrieval_note: str
    agent_has_tools: bool
    asker_background: str


class OverallState(InputState, OutputState, TurnState):
    """节点共享的完整内部状态（含 managed remaining_steps）。"""

    remaining_steps: NotRequired[RemainingSteps]
    session_summary: NotRequired[str]
    summary_upto_message_id: NotRequired[str]
    working_memory: NotRequired[dict]


KnowSphereState = OverallState

__all__ = [
    "InputState",
    "KnowSphereState",
    "OutputState",
    "OverallState",
    "TurnState",
]
