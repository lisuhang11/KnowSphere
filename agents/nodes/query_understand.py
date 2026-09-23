"""query_understand：LLM 改写 query + 意图分类。

配了 OPENJEV_API_KEY 时，意图走官方 /v1/systemone；否则读聊天模型 logprobs。
厂商不支持 logprobs、或官方接口失败时，回退为 JSON 结构化输出。改写仍生成文本。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from agents.state import KnowSphereState
from config.settings import settings
from models import create_chat_model, create_vlm_model
from models.decision import ChoiceResult, classify_choice
from models.openjev import classify_openjev
from prompts.intent_choice import (
    INTENT_OPENJEV_CRITERIA,
    build_intent_choice_prompts,
    build_intent_openjev_instructions,
    build_intent_openjev_state,
)
from prompts.intent_prompts import intent_system_prompt
from prompts.query_understand import build_query_understand_prompts
from schemas.query import (
    INTENT_CHOICE_OPTIONS,
    QueryRewriteOutput,
    QueryUnderstandOutput,
    SKIP_REWRITE_INTENTS,
    fallback_intent,
    needs_agent_tools,
    normalize_intent,
    parse_query_understand_json,
    sanitize_rewrite_query,
)
from tools.retrieval.doc_retrieval import _emit_thinking
from utils.language import answer_language_from_state
from utils.message_content import message_text
from utils.query_understand_images import (
    build_multimodal_user_content,
    load_image_data_uris_from_message,
)
from utils.run_config import (
    chat_model_kwargs_from_config,
    thread_id_from_config,
    vlm_model_id_from_config,
)

logger = logging.getLogger(__name__)

_LLM_KWARGS: dict = {
    "temperature": 0.3,
    "extra_body": {"enable_thinking": False},
}


@dataclass
class _TextUnderstandResult:
    rewrite_query: str
    intent: str
    image_description: str = ""
    intent_confidence: float | None = None
    intent_probs: dict[str, float] | None = None


def _intent_classifier_mode() -> str:
    raw = getattr(settings, "intent_classifier", "choice")
    if not isinstance(raw, str):
        return "choice"
    mode = raw.strip().lower()
    if mode in {"structured", "json"}:
        return "structured"
    return "choice"


def _openjev_api_key() -> str:
    raw = getattr(settings, "openjev_api_key", "")
    return raw.strip() if isinstance(raw, str) else ""


def _classify_intent(
    llm: Any,
    *,
    choice_system: str,
    choice_user: str,
    openjev_state: dict[str, str],
    web_search_enabled: bool,
    config: RunnableConfig,
) -> ChoiceResult | None:
    """官方 Jev 优先。失败或未配 Key 时读 logprobs。"""
    key = _openjev_api_key()
    if key:
        try:
            hosted = classify_openjev(
                state=openjev_state,
                criteria=INTENT_OPENJEV_CRITERIA,
                instructions=build_intent_openjev_instructions(
                    web_search_enabled=web_search_enabled,
                ),
                api_key=key,
                base_url=str(getattr(settings, "openjev_base_url", "") or ""),
                model=str(getattr(settings, "openjev_model", "") or ""),
                timeout_sec=float(getattr(settings, "openjev_timeout_sec", 20) or 20),
            )
        except Exception as exc:
            logger.warning("OpenJEV 意图分类失败，回退 logprobs: %s", exc)
            hosted = None
        if hosted is not None:
            return hosted
        logger.info("OpenJEV 未返回意图，回退 logprobs")
    return classify_choice(
        llm,
        [
            {"role": "system", "content": choice_system},
            {"role": "user", "content": choice_user},
        ],
        INTENT_CHOICE_OPTIONS,
        config=config,
    )


def _format_intent_thinking(intent: str, confidence: float | None) -> str:
    if isinstance(confidence, (int, float)) and 0 < float(confidence) <= 1:
        return f"{intent} ({float(confidence):.2f})"
    return intent


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


def _web_search_on(state: KnowSphereState | dict) -> bool:
    v = state.get("web_search_enabled")
    if v is None:
        return True
    return bool(v)


def _agent_has_tools(state: KnowSphereState | dict) -> bool:
    return bool(state.get("agent_has_tools"))


def _apply_intent_side_effects(
    result: dict,
    *,
    kb_selected: bool,
    web_search_enabled: bool = True,
    agent_has_tools: bool = False,
    language: str | None = None,
) -> dict:
    """非检索意图写入专用系统提示覆盖。"""
    intent = result.get("intent")
    if needs_agent_tools(
        intent,
        kb_selected,
        web_search_enabled=web_search_enabled,
        agent_has_tools=agent_has_tools,
    ):
        return {"system_prompt_override": ""}
    override = intent_system_prompt(intent, language=language)
    if override:
        return {"system_prompt_override": override}
    return {"system_prompt_override": ""}


def _query_understand_llm(config: RunnableConfig):
    model_name = (settings.query_understand_model or "").strip() or None
    llm_kwargs = chat_model_kwargs_from_config(config, _LLM_KWARGS)
    if model_name:
        llm_kwargs["model"] = model_name
    return create_chat_model(**llm_kwargs)


def _invoke_text_query_understand(
    system_prompt: str,
    user_prompt: str,
    config: RunnableConfig,
    *,
    choice_system: str | None = None,
    choice_user: str | None = None,
    openjev_state: dict[str, str] | None = None,
    web_search_enabled: bool = True,
    original_query: str = "",
) -> QueryUnderstandOutput | _TextUnderstandResult | None:
    llm = _query_understand_llm(config)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    choice = None
    if _intent_classifier_mode() == "choice" and choice_system and choice_user:
        try:
            choice = _classify_intent(
                llm,
                choice_system=choice_system,
                choice_user=choice_user,
                openjev_state=openjev_state or {},
                web_search_enabled=web_search_enabled,
                config=config,
            )
        except Exception as exc:
            logger.warning("Choice 意图分类失败，回退 JSON: %s", exc)
            choice = None

    if choice is not None:
        rewrite = original_query
        image_description = ""
        if choice.label not in SKIP_REWRITE_INTENTS:
            out = llm.with_structured_output(QueryRewriteOutput).invoke(
                messages,
                config=config,
            )
            rewrite = getattr(out, "rewrite_query", None) or original_query
            image_description = (getattr(out, "image_description", None) or "").strip()
        return _TextUnderstandResult(
            rewrite_query=rewrite,
            intent=choice.label,
            image_description=image_description,
            intent_confidence=choice.confidence,
            intent_probs=dict(choice.probs),
        )

    return llm.with_structured_output(QueryUnderstandOutput).invoke(
        messages,
        config=config,
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
        config=config,
    )
    return parse_query_understand_json(message_text(getattr(resp, "content", "")))


def query_understand(state: KnowSphereState, config: RunnableConfig) -> dict:
    current_query = (state.get("current_query") or "").strip()
    if not current_query:
        return {}

    kb_selected = bool(state.get("kb_selected"))
    web_on = _web_search_on(state)
    language = answer_language_from_state(state, current_query)
    history_pairs = list(state.get("history_pairs") or [])
    has_images = bool(state.get("has_images"))
    has_attachments = bool(state.get("has_attachments"))
    session_id = thread_id_from_config(config) or ""

    result: dict = {
        "rewrite_query": current_query,
        "answer_language": language,
        "intent": fallback_intent(
            kb_selected=kb_selected,
            has_images=has_images,
            has_attachments=has_attachments,
        ),
        "intent_confidence": None,
        "intent_probs": None,
    }

    if not settings.enable_rewrite and not has_images and not has_attachments:
        _emit_thinking(
            "【1/5 查询理解】改写已关闭\n"
            f"原问题：{current_query}\n"
            f"检索词：{current_query}\n"
            f"意图：{_format_intent_thinking(result['intent'], result.get('intent_confidence'))}"
            + (
                " → 进入工具推理"
                if needs_agent_tools(
                    result["intent"],
                    kb_selected,
                    web_search_enabled=web_on,
                    agent_has_tools=_agent_has_tools(state),
                )
                else " → 直接生成"
            ),
            None,
        )
        result.update(
            _apply_intent_side_effects(
                result,
                kb_selected=kb_selected,
                web_search_enabled=web_on,
                agent_has_tools=_agent_has_tools(state),
                language=language,
            )
        )
        return result

    asker_background = str(state.get("asker_background") or "")
    working_memory = state.get("working_memory") if isinstance(state.get("working_memory"), dict) else None
    session_summary = str(state.get("session_summary") or "")
    system_prompt, user_prompt = build_query_understand_prompts(
        query=current_query,
        history_pairs=history_pairs,
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
        web_search_enabled=web_on,
        session_summary=session_summary,
        working_memory=working_memory,
        language=language,
        asker_background=asker_background,
    )
    openjev_state = build_intent_openjev_state(
        query=current_query,
        history_pairs=history_pairs,
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
        web_search_enabled=web_on,
        session_summary=session_summary,
        working_memory=working_memory,
        asker_background=asker_background,
    )
    choice_system, choice_user = build_intent_choice_prompts(
        query=current_query,
        history_pairs=history_pairs,
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
        web_search_enabled=web_on,
        session_summary=session_summary,
        working_memory=working_memory,
        asker_background=asker_background,
    )

    rewrite = current_query
    intent = result["intent"]
    image_description = ""
    intent_confidence: float | None = None
    intent_probs: dict[str, float] | None = None

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
            out = _invoke_text_query_understand(
                system_prompt,
                user_prompt,
                config,
                choice_system=choice_system,
                choice_user=choice_user,
                openjev_state=openjev_state,
                web_search_enabled=web_on,
                original_query=current_query,
            )
            rewrite = sanitize_rewrite_query(
                (getattr(out, "rewrite_query", None) or "").strip(),
                current_query,
            )
            intent = getattr(out, "intent", None) or intent
            image_description = (getattr(out, "image_description", None) or "").strip()
            raw_conf = getattr(out, "intent_confidence", None)
            if isinstance(raw_conf, (int, float)):
                intent_confidence = float(raw_conf)
            raw_probs = getattr(out, "intent_probs", None)
            if isinstance(raw_probs, dict):
                intent_probs = {str(k): float(v) for k, v in raw_probs.items() if isinstance(v, (int, float))}

        if rewrite:
            result["rewrite_query"] = rewrite
        result["intent"] = normalize_intent(
            intent,
            kb_selected=kb_selected,
            has_images=has_images,
            has_attachments=has_attachments,
        )
        result["intent_confidence"] = intent_confidence
        result["intent_probs"] = intent_probs
        # 始终写入（含空串），覆盖 checkpoint 中上一轮残留
        result["image_description"] = image_description
    except Exception as exc:
        logger.warning("query_understand 失败，降级原 query: %s", exc)
        _emit_thinking("【1/5 查询理解】LLM 失败，降级使用原问题。", None)
        result.update(
            _apply_intent_side_effects(
                result,
                kb_selected=kb_selected,
                web_search_enabled=web_on,
                agent_has_tools=_agent_has_tools(state),
                language=language,
            )
        )
        return result

    thinking_extra = ""
    if result.get("image_description"):
        thinking_extra = "\n已生成图片描述（VLM）"
    if asker_background:
        thinking_extra += "\n已注入跨会话背景（asker_background）"

    _emit_thinking(
        "【1/5 查询理解】\n"
        f"原问题：{current_query}\n"
        f"改写检索词：{result['rewrite_query']}\n"
        f"意图：{_format_intent_thinking(result['intent'], result.get('intent_confidence'))}"
        + (
            " → 进入工具推理"
            if needs_agent_tools(
                result["intent"],
                kb_selected,
                web_search_enabled=web_on,
                agent_has_tools=_agent_has_tools(state),
            )
            else " → 直接生成"
        )
        + thinking_extra,
        None,
    )
    result.update(
        _apply_intent_side_effects(
            result,
            kb_selected=kb_selected,
            web_search_enabled=web_on,
            agent_has_tools=_agent_has_tools(state),
            language=language,
        )
    )
    return result


def route_after_understand(state: KnowSphereState) -> str:
    """条件边：需要工具 → agent（ReAct）；否则 → generate。"""
    if needs_agent_tools(
        state.get("intent"),
        bool(state.get("kb_selected")),
        web_search_enabled=_web_search_on(state),
        agent_has_tools=_agent_has_tools(state),
    ):
        return "agent"
    return "generate"
