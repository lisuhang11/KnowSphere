"""query_understand：LLM 改写 query + 意图分类。"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from config.settings import settings
from models import create_chat_model, create_vlm_model
from prompts.intent_prompts import intent_system_prompt
from prompts.query_understand import build_query_understand_prompts
from schemas.query import (
    QueryUnderstandOutput,
    fallback_intent,
    needs_retrieval,
    normalize_intent,
    parse_query_understand_json,
    sanitize_rewrite_query,
)
from states import KnowSphereState
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.message_content import message_text
from utils.query_understand_images import (
    build_multimodal_user_content,
    load_image_data_uris_from_message,
)
from utils.run_config import chat_model_kwargs_from_config, vlm_model_id_from_config

logger = logging.getLogger(__name__)

_LLM_KWARGS: dict = {
    "temperature": 0.3,
    "extra_body": {"enable_thinking": False},
}


def _resolve_vlm_model_id(config: RunnableConfig | None = None) -> str | None:
    from_config = vlm_model_id_from_config(config)
    if from_config:
        return from_config
    explicit = (settings.chat_vlm_model_id or "").strip()
    if explicit:
        return explicit
    try:
        from utils.model_store import ModelStore

        rec = ModelStore().get_default_model("VLLM")
        return rec["id"] if rec else None
    except Exception:
        return None


def _last_human_message(state: KnowSphereState) -> HumanMessage | None:
    messages = list(state.get("messages") or [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg
    return None


def _apply_intent_side_effects(result: dict, *, kb_selected: bool) -> dict:
    """非检索意图写入专用系统提示覆盖。"""
    intent = result.get("intent")
    if needs_retrieval(intent, kb_selected):
        return {"system_prompt_override": ""}
    override = intent_system_prompt(intent)
    if override:
        return {"system_prompt_override": override}
    return {"system_prompt_override": ""}


def _invoke_text_query_understand(
    system_prompt: str,
    user_prompt: str,
    config: RunnableConfig,
) -> QueryUnderstandOutput | None:
    model_name = (settings.query_understand_model or "").strip() or None
    llm_kwargs = chat_model_kwargs_from_config(config, _LLM_KWARGS)
    if model_name:
        llm_kwargs["model"] = model_name
    llm = create_chat_model(**llm_kwargs).with_structured_output(QueryUnderstandOutput)
    return llm.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        config={"callbacks": []},
    )


def _invoke_multimodal_query_understand(
    system_prompt: str,
    user_prompt: str,
    image_data_uris: list[str],
    config: RunnableConfig,
) -> dict[str, str] | None:
    model_id = _resolve_vlm_model_id(config)
    if not model_id:
        logger.warning("未配置 VLLM，多模态 query_understand 降级为文本")
        return None

    llm_kwargs = chat_model_kwargs_from_config(config, {"temperature": 0.3})
    llm_kwargs.pop("extra_body", None)
    try:
        llm = create_vlm_model(model=model_id, **llm_kwargs)
    except Exception as exc:
        logger.warning("创建 VLLM 失败: %s", exc)
        return None

    user_content = build_multimodal_user_content(user_prompt, image_data_uris)
    resp = llm.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        config={"callbacks": []},
    )
    return parse_query_understand_json(message_text(getattr(resp, "content", "")))


def query_understand(state: KnowSphereState, config: RunnableConfig) -> dict:
    current_query = (state.get("current_query") or "").strip()
    if not current_query:
        return {}

    kb_selected = bool(state.get("kb_selected"))
    history_pairs = list(state.get("history_pairs") or [])
    has_images = bool(state.get("has_images"))
    has_attachments = bool(state.get("has_attachments"))
    session_id = str((config.get("configurable") or {}).get("thread_id") or "")

    result: dict = {
        "rewrite_query": current_query,
        "intent": fallback_intent(
            kb_selected=kb_selected,
            has_images=has_images,
            has_attachments=has_attachments,
        ),
    }

    if not settings.enable_rewrite and not has_images and not has_attachments:
        _emit_thinking(
            "【1/5 查询理解】改写已关闭\n"
            f"原问题：{current_query}\n"
            f"检索词：{current_query}\n"
            f"意图：{result['intent']}"
            + (
                " → 需要检索知识库"
                if needs_retrieval(result["intent"], kb_selected)
                else " → 跳过检索"
            ),
            None,
        )
        result.update(_apply_intent_side_effects(result, kb_selected=kb_selected))
        return result

    system_prompt, user_prompt = build_query_understand_prompts(
        query=current_query,
        history_pairs=history_pairs,
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
    )

    rewrite = current_query
    intent = result["intent"]
    image_description = ""

    try:
        parsed_mm: dict[str, str] | None = None
        if has_images:
            human = _last_human_message(state)
            image_uris: list[str] = []
            if human is not None:
                image_uris = load_image_data_uris_from_message(human, session_id=session_id)
            if image_uris:
                parsed_mm = _invoke_multimodal_query_understand(
                    system_prompt, user_prompt, image_uris, config
                )

        if parsed_mm:
            rewrite = sanitize_rewrite_query(
                parsed_mm.get("rewrite_query", "").strip(),
                current_query,
            )
            intent = parsed_mm.get("intent") or intent
            image_description = (parsed_mm.get("image_description") or "").strip()
        else:
            out = _invoke_text_query_understand(system_prompt, user_prompt, config)
            rewrite = sanitize_rewrite_query(
                (getattr(out, "rewrite_query", None) or "").strip(),
                current_query,
            )
            intent = getattr(out, "intent", None) or intent
            image_description = (getattr(out, "image_description", None) or "").strip()

        if rewrite:
            result["rewrite_query"] = rewrite
        result["intent"] = normalize_intent(
            intent,
            kb_selected=kb_selected,
            has_images=has_images,
            has_attachments=has_attachments,
        )
        # 始终写入（含空串），覆盖 checkpoint 中上一轮残留
        result["image_description"] = image_description
    except Exception as exc:
        logger.warning("query_understand 失败，降级原 query: %s", exc)
        _emit_thinking("【1/5 查询理解】LLM 失败，降级使用原问题。", None)
        result.update(_apply_intent_side_effects(result, kb_selected=kb_selected))
        return result

    thinking_extra = ""
    if result.get("image_description"):
        thinking_extra = "\n已生成图片描述（VLM）"

    _emit_thinking(
        "【1/5 查询理解】\n"
        f"原问题：{current_query}\n"
        f"改写检索词：{result['rewrite_query']}\n"
        f"意图：{result['intent']}"
        + (
            " → 需要检索知识库"
            if needs_retrieval(result["intent"], kb_selected)
            else " → 跳过检索"
        )
        + thinking_extra,
        None,
    )
    result.update(_apply_intent_side_effects(result, kb_selected=kb_selected))
    return result


def route_after_understand(state: KnowSphereState) -> str:
    """条件边：需要检索 → prefetch_retrieval，否则 → agent。"""
    if needs_retrieval(state.get("intent"), bool(state.get("kb_selected"))):
        return "prefetch_retrieval"
    return "agent"
