"""generate 节点：一次生成，不绑定工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from models import create_chat_model
from states import KnowSphereState
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.agent_runtime import resolve_system_prompt
from utils.citation import citation_payload_from_source_dicts
from utils.run_config import chat_model_kwargs_from_config, kb_ids_from_config
from utils.short_term_memory import memory_system_suffix_from_state, memory_view_from_state


def _prepare_messages(
    system_prompt: str,
    messages: list[BaseMessage],
    config: RunnableConfig | None,
    *,
    system_prompt_override: str | None = None,
    memory_suffix: str | None = None,
) -> list[BaseMessage]:
    """组装系统消息。非检索意图优先使用 query_understand 写入的 override。"""
    from config.settings import settings
    from prompts import PURE_CHAT_SYSTEM_PROMPT, build_rag_system_prompt

    extra = (memory_suffix or "").strip()
    if system_prompt_override:
        base = system_prompt_override.strip()
    else:
        kb_ids = kb_ids_from_config(config)
        if kb_ids:
            base = build_rag_system_prompt(enable_citation=settings.citation_enabled)
        else:
            base = (system_prompt or "").strip() or PURE_CHAT_SYSTEM_PROMPT.strip()
    if extra:
        base = f"{base.rstrip()}\n\n{extra}"
    return [SystemMessage(content=base)] + list(messages)


def _delta_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def _inject_image_description(messages: list[BaseMessage], image_description: str) -> list[BaseMessage]:
    """将 query_understand VLM 输出的图片描述注入最后一条用户消息（仅本轮 LLM 入参）。"""
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


def _append_context_block(messages: list[BaseMessage], context_block: str) -> list[BaseMessage]:
    block = (context_block or "").strip()
    if not block:
        return messages
    out = list(messages)
    for idx in range(len(out) - 1, -1, -1):
        msg = out[idx]
        if not isinstance(msg, HumanMessage):
            continue
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "【知识库检索结果】" in text:
            return out
        new_msg = HumanMessage(content=f"{text.rstrip()}\n\n{block}".strip())
        kwargs = dict(getattr(msg, "additional_kwargs", None) or {})
        if kwargs:
            new_msg.additional_kwargs = kwargs
        out[idx] = new_msg
        break
    else:
        out.append(HumanMessage(content=block))
    return out


def _llm_messages(state: KnowSphereState, config: RunnableConfig, system_prompt: str) -> list[BaseMessage]:
    window = memory_view_from_state(state).window_messages
    messages = _inject_image_description(
        window,
        str(state.get("image_description") or ""),
    )
    messages = _append_context_block(messages, str(state.get("context_block") or ""))
    return _prepare_messages(
        system_prompt,
        messages,
        config,
        system_prompt_override=state.get("system_prompt_override"),
        memory_suffix=memory_system_suffix_from_state(state),
    )


def _with_ks_citations(state: KnowSphereState, response: AIMessage) -> AIMessage:
    cites = citation_payload_from_source_dicts(state.get("last_sources") or [])
    if not cites:
        return response
    kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    kwargs["ks_citations"] = cites
    response.additional_kwargs = kwargs
    return response


def call_generate(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = resolve_system_prompt(config, system_prompt)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    messages = _llm_messages(state, config, prompt)
    _emit_thinking("正在生成回答…", None)
    response = model.invoke(messages, config)
    if not isinstance(response, AIMessage):
        response = AIMessage(content=str(response))
    return {"messages": [_with_ks_citations(state, response)]}


async def acall_generate(
    state: KnowSphereState,
    config: RunnableConfig,
    *,
    system_prompt: str,
    chat_model_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = resolve_system_prompt(config, system_prompt)
    model = create_chat_model(**chat_model_kwargs_from_config(config, chat_model_kwargs))
    messages = _llm_messages(state, config, prompt)
    _emit_thinking("正在生成回答…", None)
    parts: list[str] = []
    last_chunk: Any = None
    async for chunk in model.astream(messages, config):
        last_chunk = chunk
        text = _delta_text(chunk)
        if text:
            parts.append(text)
    content = "".join(parts)
    if content:
        response = AIMessage(content=content)
    elif last_chunk is not None and isinstance(last_chunk, AIMessage):
        response = last_chunk
    else:
        response = await model.ainvoke(messages, config)
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(response))
    return {"messages": [_with_ks_citations(state, response)]}
