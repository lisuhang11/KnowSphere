"""agent 节点：按 configurable.kb_ids 动态绑定工具，调用主模型。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from models import create_chat_model
from states import KnowSphereState
from tools import get_tools
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.run_config import chat_model_kwargs_from_config, kb_ids_from_config


def _are_more_steps_needed(state: KnowSphereState, response: AIMessage) -> bool:
    """步数不足时禁止继续 tool_calls（对齐 create_react_agent 语义）。"""
    if not response.tool_calls:
        return False
    remaining = state.get("remaining_steps")
    if remaining is not None and remaining < 2:
        return True
    return False


def _tools_for_config(
    config: RunnableConfig | None,
    tool_list: list[Any],
    state: KnowSphereState | None = None,
) -> list[Any]:
    """未选知识库或非检索意图时不绑定 doc_retrieval。"""
    if state and (state.get("system_prompt_override") or "").strip():
        return []
    kb_ids = kb_ids_from_config(config)
    if not kb_ids:
        return []
    return tool_list


def _prepare_messages(
    system_prompt: str,
    messages: list[BaseMessage],
    config: RunnableConfig | None,
    *,
    intent: str | None = None,
    system_prompt_override: str | None = None,
) -> list[BaseMessage]:
    """组装系统消息。非检索意图时优先使用 query_understand 写入的 override。"""
    base = (system_prompt_override or "").strip() or system_prompt
    kb_ids = kb_ids_from_config(config)

    if system_prompt_override:
        return [SystemMessage(content=base)] + list(messages)

    if kb_ids:
        tail = (
            "\n\n【本轮已限定知识库】"
            "回答人物/经历/项目等问题时，仅依据 doc_retrieval / query_knowledge_graph 结果"
            "或已注入的检索工具消息；"
            "关系型问题（A 与 B 的关系、某实体关联概念）可优先 query_knowledge_graph；"
            "禁止使用互联网公开常识（同名公众人物等）臆测；检索无相关内容时明确说明未找到。"
        )
        if intent in ("follow_up", "summarize"):
            tail += "\n【本轮无需新检索】优先依据对话历史作答，不要调用 doc_retrieval。"
        elif intent == "clarification":
            tail += (
                "\n【本轮问题含糊】用户未说明具体指代，不要调用 doc_retrieval；"
                "礼貌请用户补充上下文或提出更具体的问题。"
            )
        elif intent in ("greeting", "chitchat"):
            tail += "\n【本轮为问候/闲聊】不要调用 doc_retrieval。"
        elif intent == "image_only":
            tail += "\n【本轮为图片理解】不要调用 doc_retrieval，依据消息中的图片分析结果作答。"
        elif intent == "doc_only":
            tail += "\n【本轮为附件理解】不要调用 doc_retrieval，依据消息中的 [会话附件内容] 作答。"
    else:
        tail = (
            "\n\n【本轮未选择知识库】无法检索知识库。"
            "若消息中已有 [会话附件内容] 或图片说明，请依据这些内容作答；"
            "对知识库中的人物、项目等具体问题，须提示用户在输入框上方选择知识库；"
            "禁止凭公开资料作答（尤其同名人物）。"
        )
    return [SystemMessage(content=base + tail)] + list(messages)


def _inject_image_description(messages: list[BaseMessage], image_description: str) -> list[BaseMessage]:
    """将 query_understand VLM 输出的图片描述注入最后一条用户消息。"""
    desc = (image_description or "").strip()
    if not desc:
        return messages
    out = list(messages)
    for idx in range(len(out) - 1, -1, -1):
        msg = out[idx]
        if not isinstance(msg, HumanMessage):
            continue
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "[用户上传图片内容]" in text:
            return out
        prefix = text.strip() or "请分析上传的图片"
        new_content = f"{prefix}\n\n[用户上传图片内容]\n{desc}".strip()
        new_msg = HumanMessage(content=new_content)
        kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
        if kwargs:
            new_msg.additional_kwargs = kwargs
        out[idx] = new_msg
        break
    return out


def call_agent(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    tool_list: list[Any],
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = _tools_for_config(config, tool_list, state)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    if tools:
        model = model.bind_tools(tools)

    messages = _inject_image_description(
        list(state["messages"]),
        str(state.get("image_description") or ""),
    )
    messages = _prepare_messages(
        system_prompt,
        messages,
        config,
        intent=state.get("intent"),
        system_prompt_override=state.get("system_prompt_override"),
    )
    _emit_thinking("正在生成回答…", None)
    response = model.invoke(messages, config)
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response))

    if _are_more_steps_needed(state, response):
        response = AIMessage(
            id=response.id,
            content="抱歉，处理该请求需要更多步骤，请简化问题或拆分后再试。",
        )
    return {"messages": [response]}


async def acall_agent(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    tool_list: list[Any],
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = _tools_for_config(config, tool_list, state)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    if tools:
        model = model.bind_tools(tools)

    messages = _inject_image_description(
        list(state["messages"]),
        str(state.get("image_description") or ""),
    )
    messages = _prepare_messages(
        system_prompt,
        messages,
        config,
        intent=state.get("intent"),
        system_prompt_override=state.get("system_prompt_override"),
    )
    _emit_thinking("正在生成回答…", None)
    response = await model.ainvoke(messages, config)
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response))

    if _are_more_steps_needed(state, response):
        response = AIMessage(
            id=response.id,
            content="抱歉，处理该请求需要更多步骤，请简化问题或拆分后再试。",
        )
    return {"messages": [response]}
