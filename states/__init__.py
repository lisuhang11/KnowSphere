"""状态与配置 schema。

分层（对齐 LangGraph input / overall / output）：
- InputState：invoke 入参契约（仅 messages）
- OutputState：invoke 对外返回（messages + last_sources）
- TurnState：单轮 query pipeline 临时通道（prepare_context 入口重置）
- OverallState / KnowSphereState：节点共享的完整内部状态
- AgentConfig：经 config["configurable"] 注入（挂为 context_schema）
"""

from __future__ import annotations

from typing import Annotated, NotRequired, Sequence, TypedDict

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


class OverallState(InputState, OutputState, TurnState):
    """节点共享的完整内部状态（含 managed remaining_steps）。"""

    remaining_steps: NotRequired[RemainingSteps]
    # 短期记忆（跨轮保留；prepare_context 不得清空）
    session_summary: NotRequired[str]
    summary_upto_message_id: NotRequired[str]
    working_memory: NotRequired[dict]


# 兼容既有 import；语义等同 OverallState
KnowSphereState = OverallState


class AgentConfig(TypedDict, total=False):
    """运行时可配置参数（经 configurable 传入；StateGraph context_schema）。"""

    thread_id: str  # 会话 ID（checkpointer 记忆键）
    kb_ids: list[int]  # 会话选定的知识库范围；空/缺省 = 不检索
    chat_model_id: str  # 本轮主对话模型
    vlm_model_id: str  # 本轮 VLM（多模态 query_understand）
    agent_id: str  # 本轮智能体（工具 + 提示词）
    web_search_enabled: bool  # 本轮输入框联网开关（管理员总开关仍生效）
    graph_enabled: bool  # 所选知识库是否已开图谱且 Neo4j 可用


__all__ = [
    "InputState",
    "OutputState",
    "TurnState",
    "OverallState",
    "KnowSphereState",
    "AgentConfig",
]
