"""L2 语义压缩：超限工具结果用 LLM 蒸馏；失败则结构化截断。

preview 禁止走这条链路——只允许原文 substring（见 tool_result_store.build_preview）。
LLM 只产出给后续推理用的结论，不重写 JSON 字段名、不重建数据结构。
"""

from __future__ import annotations

import json
import logging

from langchain_core.runnables import RunnableConfig

from config.settings import settings
from utils.message_content import message_text

logger = logging.getLogger(__name__)

COMPRESS_SYSTEM_PROMPT = """You distill one tool result for a later reasoning step.
Extract only what later steps need: IDs, names, numbers, statuses, counts, and concrete conclusions.
Copy identifier strings exactly. Do not invent facts or field names.
Do not rewrite JSON keys. Do not reconstruct the original JSON.
Do not write a preview, excerpt, or dump of the raw structure — that is stored elsewhere.
Output plain text only, no markdown fences."""


def should_semantic_compress(content_len: int, *, char_limit: int | None = None) -> bool:
    if not settings.tool_result_compress_enabled:
        return False
    limit = (
        settings.tool_result_compress_char_limit if char_limit is None else char_limit
    )
    return content_len > limit


def build_fallback_truncated(
    text: str,
    *,
    tool_name: str,
    original_length: int,
    max_chars: int | None = None,
) -> str:
    """LLM 失败时的确定性降级：原文 substring + JSON 包装，不当成全文。"""
    cap = (
        settings.tool_result_compress_fallback_chars if max_chars is None else max_chars
    )
    body = text or ""
    return json.dumps(
        {
            "__fallbackTruncated": True,
            "__toolType": tool_name or "tool",
            "__originalLength": original_length,
            "content": body[: max(0, cap)],
        },
        ensure_ascii=False,
    )


def is_fallback_truncated(value: str) -> bool:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(parsed, dict) and parsed.get("__fallbackTruncated") is True


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _invoke_compressor(text: str, tool_name: str, config: RunnableConfig | None) -> str:
    from models import create_chat_model
    from utils.run_config import chat_model_kwargs_from_config

    out_cap = max(200, int(settings.tool_result_compress_output_chars))
    in_cap = max(out_cap, int(settings.tool_result_compress_input_chars))
    source = text if len(text) <= in_cap else text[:in_cap] + "\n…(input truncated for distillation)"
    model_ref = (settings.tool_result_compress_model or "").strip()
    base = {
        "temperature": float(settings.tool_result_compress_temperature),
        "timeout": float(settings.tool_result_compress_timeout_sec),
        "max_tokens": 1200,
        "max_retries": 0,
        "extra_body": {"enable_thinking": False},
    }
    if model_ref:
        base["model"] = model_ref
    llm = create_chat_model(**chat_model_kwargs_from_config(config, base))
    resp = llm.invoke(
        [
            {"role": "system", "content": COMPRESS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Tool: {tool_name or 'tool'}\n"
                    f"Original length: {len(text)} characters.\n"
                    f"Distill to at most {out_cap} characters.\n\n{source}"
                ),
            },
        ],
        config=config,
    )
    distilled = message_text(getattr(resp, "content", ""))
    if not distilled:
        raise ValueError("empty compression")
    return _clip(distilled, out_cap)


def semantic_compress(
    text: str,
    *,
    tool_name: str,
    original_length: int,
    config: RunnableConfig | None = None,
) -> str:
    """成功返回 ≤2000 字结论；失败返回 __fallbackTruncated JSON。"""
    try:
        from tools.events import emit_thinking

        emit_thinking("正在压缩过长的工具结果…")
        return _invoke_compressor(text, tool_name, config)
    except Exception:
        logger.warning("语义压缩失败，改用结构化截断 tool=%s", tool_name, exc_info=True)
        return build_fallback_truncated(
            text, tool_name=tool_name, original_length=original_length
        )
