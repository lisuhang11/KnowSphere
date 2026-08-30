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

_META_REWRITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"请在知识库"),
    re.compile(r"请搜索"),
    re.compile(r"在知识库中查找"),
    re.compile(r"在知识库查找"),
    re.compile(r"请重新在知识库"),
    re.compile(r"请帮我查(?:一下)?知识库"),
    re.compile(r"请查找知识库"),
)

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


def fallback_intent(
    *,
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
) -> str:
    """无 LLM 标签时的默认意图。会话附件不依赖知识库。"""
    if kb_selected:
        return "kb_search"
    if has_attachments:
        return "doc_only"
    if has_images:
        return "image_only"
    return "no_kb"


def normalize_intent(
    intent: str | None,
    *,
    kb_selected: bool,
    has_images: bool = False,
    has_attachments: bool = False,
) -> str:
    """只校正标签与附件标记的硬约束，不根据问句措辞重判意图。

    未选知识库时：不能 kb_search，但必须保留 doc_only / image_only
    （会话临时附件不在知识库里，WeKnora 同样跳过检索并直接读附件正文）。
    """
    label = (intent or "").strip() or fallback_intent(
        kb_selected=kb_selected,
        has_images=has_images,
        has_attachments=has_attachments,
    )
    if label == "image_only" and not has_images:
        label = "kb_search" if kb_selected else "no_kb"
    if label == "doc_only" and not has_attachments:
        label = "kb_search" if kb_selected else "no_kb"
    if not kb_selected and label == "kb_search":
        return "no_kb"
    return label


def needs_retrieval(intent: str | None, kb_selected: bool) -> bool:
    """是否应走 prefetch / doc_retrieval。

    对齐 WeKnora：只看意图标签 + 是否选了知识库，不再用原问句正则二次判决。
    KnowSphere 仅 kb_search 检索（greeting/follow_up/image_only 等一律跳过）。
    """
    if not kb_selected:
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
