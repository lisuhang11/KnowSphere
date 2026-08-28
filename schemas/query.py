"""Query understanding 结构化输出。"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

QueryIntent = Literal[
    "kb_search",
    "follow_up",
    "greeting",
    "chitchat",
    "no_kb",
    "clarification",
    "summarize",
    "image_only",
    "doc_only",
]

RETRIEVAL_INTENTS: frozenset[str] = frozenset({"kb_search"})

NON_RETRIEVAL_INTENTS: frozenset[str] = frozenset(
    {
        "follow_up",
        "greeting",
        "chitchat",
        "clarification",
        "summarize",
        "image_only",
        "doc_only",
        "no_kb",
    }
)

# 不含有效检索词的含糊问句（无历史指代时可判定）
_VAGUE_QUERY_PATTERNS: tuple[str, ...] = (
    "这是什么",
    "这是啥",
    "啥意思",
    "什么意思",
    "那个呢",
    "这个呢",
    "然后呢",
    "还有呢",
    "怎么说",
    "讲一讲",
    "说说看",
    "介绍一下",
    "能详细说说吗",
    "能再说说吗",
)

_META_REWRITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请在知识库"),
    re.compile(r"请搜索"),
    re.compile(r"在知识库中查找"),
    re.compile(r"在知识库查找"),
    re.compile(r"请重新在知识库"),
    re.compile(r"请帮我查(?:一下)?知识库"),
    re.compile(r"请查找知识库"),
)

def is_vague_query(query: str, history_pairs: list | None = None) -> bool:
    """问题过于含糊、无法形成有效检索词（如孤立的「这是什么」）。"""
    q = query.strip().lower()
    if not q:
        return True
    # 有对话历史时，「这是什么」可能指代上文，交给 LLM 判断
    if history_pairs:
        return False
    if q in _VAGUE_QUERY_PATTERNS:
        return True
    # 极短且无非实体词
    if len(q) <= 6 and not any(c.isalnum and c not in "这那啥吗呢么" for c in q):
        return True
    return False

def is_meta_rewrite(rewrite: str) -> bool:
    """改写结果是否含 meta 检索指令而非实际检索词。"""
    text = rewrite.strip()
    if not text:
        return False
    return any(p.search(text) for p in _META_REWRITE_PATTERNS)

def sanitize_rewrite_query(rewrite: str, original: str) -> str:
    """过滤 meta 指令式改写，回退原问。"""
    cleaned = rewrite.strip()
    if not cleaned:
        return original.strip()
    if is_meta_rewrite(cleaned):
        return original.strip()
    return cleaned

def needs_retrieval(
    intent: str | None,
    kb_selected: bool,
    *,
    query: str | None = None,
    history_pairs: list | None = None,
) -> bool:
    """是否应走 prefetch / doc_retrieval。"""
    if not kb_selected:
        return False
    if query and is_vague_query(query, history_pairs):
        return False
    if not intent:
        return True
    return intent in RETRIEVAL_INTENTS

def parse_query_understand_json(raw: str) -> dict[str, str] | None:
    """容错解析 LLM JSON。"""
    content = raw.strip()
    if not content:
        return None

    def _from_obj(obj: dict) -> dict[str, str] | None:
        if not isinstance(obj, dict):
            return None
        rewrite = ""
        for key in ("rewrite_query", "rewritten_query", "query", "question"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                rewrite = val.strip()
                break
        intent = obj.get("intent")
        intent_str = intent.strip() if isinstance(intent, str) else ""
        image_desc = ""
        for key in ("image_description", "image_desc", "description"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                image_desc = val.strip()
                break
        if not rewrite and not intent_str and not image_desc:
            return None
        out: dict[str, str] = {}
        if rewrite:
            out["rewrite_query"] = rewrite
        if intent_str:
            out["intent"] = intent_str
        if image_desc:
            out["image_description"] = image_desc
        return out

    try:
        parsed = _from_obj(json.loads(content))
        if parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return _from_obj(json.loads(content[start : end + 1]))
    except json.JSONDecodeError:
        return None

class QueryUnderstandOutput(BaseModel):
    """query_understand 节点 LLM 输出。"""

    rewrite_query: str = Field(description="指代消解后的独立检索问句")
    intent: QueryIntent = Field(description="用户意图分类")
    image_description: str = Field(default="", description="图片分析/OCR 描述（有图时必填）")
