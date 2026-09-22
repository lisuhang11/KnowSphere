"""generate 节点：一次生成，不绑定工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from agents.state import KnowSphereState
from models import create_chat_model
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.agent_runtime import resolve_system_prompt
from utils.citation import citation_payload_from_source_dicts
from utils.context_layout import assemble_model_messages, llm_history_messages
from utils.language import answer_language_from_state
from utils.run_config import chat_model_kwargs_from_config, kb_ids_from_config


def _prepare_messages(
    system_prompt: str,
    messages: list[BaseMessage],
    config: RunnableConfig | None,
    *,
    system_prompt_override: str | None = None,
    memory_suffix: str | None = None,
    answer_language: str | None = None,
    session_summary: str | None = None,
    rewrite_query: str | None = None,
    image_description: str | None = None,
    asker_background: str | None = None,
    context_block: str | None = None,
) -> list[BaseMessage]:
    """非检索意图优先使用 query_understand 写入的 override。摘要不进系统提示。"""
    from config.settings import settings
    from prompts import PURE_CHAT_SYSTEM_PROMPT, build_rag_system_prompt

    del memory_suffix
    if system_prompt_override:
        base = system_prompt_override.strip()
    else:
        kb_ids = kb_ids_from_config(config)
        if kb_ids:
            base = build_rag_system_prompt(enable_citation=settings.citation_enabled)
        else:
            base = (system_prompt or "").strip() or PURE_CHAT_SYSTEM_PROMPT.strip()
    return assemble_model_messages(
        base,
        messages,
        config,
        answer_language=answer_language,
        session_summary=session_summary,
        rewrite_query=rewrite_query,
        image_description=image_description,
        asker_background=asker_background,
        context_block=context_block,
    )


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


def _llm_messages(state: KnowSphereState, config: RunnableConfig, system_prompt: str) -> list[BaseMessage]:
    return _prepare_messages(
        system_prompt,
        llm_history_messages(state),
        config,
        system_prompt_override=state.get("system_prompt_override"),
        answer_language=answer_language_from_state(state),
        session_summary=str(state.get("session_summary") or ""),
        rewrite_query=str(state.get("rewrite_query") or ""),
        image_description=str(state.get("image_description") or ""),
        asker_background=str(state.get("asker_background") or ""),
        context_block=str(state.get("context_block") or ""),
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
