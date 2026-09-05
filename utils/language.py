"""按用户提问语种决定回答语言。只区分中文 / 英文。"""

from __future__ import annotations

import re
from typing import Any

ANSWER_LANGUAGE_ZH = "中文"
ANSWER_LANGUAGE_EN = "English"
LANGUAGE_PLACEHOLDER = "{{language}}"

# 汉字（基本区 + 扩展 A）。假名单独排除，避免日文被当成中文。
_HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_KANA_RE = re.compile(r"[\u3040-\u30ff\uff66-\uff9d]")


def is_chinese_query(text: str) -> bool:
    """提问里出现汉字且不含日文假名，视为中文。"""
    raw = text or ""
    if _KANA_RE.search(raw):
        return False
    return bool(_HAN_RE.search(raw))


def answer_language_for_query(text: str) -> str:
    """中文提问 → 中文；其余一律英文。"""
    return ANSWER_LANGUAGE_ZH if is_chinese_query(text) else ANSWER_LANGUAGE_EN


def normalize_answer_language(language: str | None) -> str:
    lang = (language or "").strip()
    if lang in (ANSWER_LANGUAGE_ZH, ANSWER_LANGUAGE_EN):
        return lang
    return ANSWER_LANGUAGE_EN


def apply_answer_language(template: str, language: str | None) -> str:
    """把 {{language}} 换成中文或 English；空值按英文。"""
    lang = normalize_answer_language(language)
    if LANGUAGE_PLACEHOLDER not in template:
        return template
    return template.replace(LANGUAGE_PLACEHOLDER, lang)


def ensure_answer_language(template: str, language: str | None) -> str:
    """替换占位符；自定义提示没有语言规则时再追加一条。"""
    lang = normalize_answer_language(language)
    text = apply_answer_language(template, lang)
    if f"ALWAYS respond in {lang}" in text:
        return text
    return text.rstrip() + f"\n\n## CRITICAL: Language Rule\n- ALWAYS respond in {lang}\n"


def answer_language_from_state(state: dict[str, Any] | None, query: str = "") -> str:
    """优先用本轮已写入的 answer_language，否则从提问重算。"""
    if state:
        raw = str(state.get("answer_language") or "").strip()
        if raw in (ANSWER_LANGUAGE_ZH, ANSWER_LANGUAGE_EN):
            return raw
        q = query or str(state.get("current_query") or "")
        if q:
            return answer_language_for_query(q)
    return answer_language_for_query(query)
