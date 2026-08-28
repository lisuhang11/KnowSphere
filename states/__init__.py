"""状态与配置 schema。

KnowSphereState 为 StateGraph 状态：messages + remaining_steps（步数上限）
+ last_sources（collect_sources 节点写入的检索来源汇总）；
AgentConfig 描述 LangGraph 运行时经 config["configurable"] 注入的运行参数
（doc_retrieval / agent 节点读取，键与 api/chat.py 注入保持一致）。
"""

from __future__ import annotations

from typing import Annotated, NotRequired, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from langgraph.managed import RemainingSteps

class KnowSphereState(TypedDict):
    """KnowSphere 对话状态。"""

    messages: Annotated[Sequence[BaseMessage], add_messages]
    remaining_steps: NotRequired[RemainingSteps]
    last_sources: NotRequired[list[dict]]  # collect_sources 汇总 doc_retrieval 命中
    # Query pipeline（每轮由 prepare_context / query_understand 写入）
    current_query: NotRequired[str]
    rewrite_query: NotRequired[str]
    intent: NotRequired[str]
    history_pairs: NotRequired[list[dict[str, str]]]
    kb_selected: NotRequired[bool]
    system_prompt_override: NotRequired[str]  # 非检索意图专用系统提示
    has_images: NotRequired[bool]  # 当前轮用户消息含图片
    has_attachments: NotRequired[bool]  # 当前轮用户消息含临时附件
    image_description: NotRequired[str]  # query_understand 多模态输出的图片描述

class AgentConfig(TypedDict, total=False):
    """运行时可配置参数（经 configurable 传入）。"""

    thread_id: str  # 会话 ID（checkpointer 记忆键）
    kb_ids: list[int]  # 会话选定的知识库范围；空/缺省 = 不检索
