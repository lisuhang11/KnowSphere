"""Query understanding 结构化输出。"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

QueryIntent = Literal[
    "kb_search",
    "web_search",
    "follow_up",
    "greeting",
    "chitchat",
    "no_kb",
    "clarification",
    "summarize",
    "image_only",
    "doc_only",
]

# 对齐 WeKnora QueryIntent.NeedsKBRetrieval：kb_search / clarification / summarize 都检索
RETRIEVAL_INTENTS: frozenset[str] = frozenset(
    {"kb_search", "clarification", "summarize"}
)

NON_RETRIEVAL_INTENTS: frozenset[str] = frozenset(
    {
        "follow_up",
        "greeting",
        "chitchat",
        "web_search",
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

# 改写若丢掉这些时效/热度词，联网检索会变成「旧闻」问句
_REWRITE_CUE_TOKENS: tuple[str, ...] = (
    "比较火",
    "很火",
    "热搜",
    "热议",
    "最新",
    "最近",
    "刚刚",
    "今天",
    "昨日",
    "昨天",
    "本周",
    "今年",
    "实时",
    "新闻",
    "刚发生",
)

def is_meta_rewrite(rewrite: str) -> bool:
    """改写结果是否含 meta 检索指令而非实际检索词。"""
    text = rewrite.strip()
    if not text:
        return False
    return any(p.search(text) for p in _META_REWRITE_PATTERNS)

def restore_rewrite_cues(rewrite: str, original: str) -> str:
    """改写丢掉「最近 / 比较火」等时效词时回退原问，避免联网检索变成旧闻。"""
    orig = original.strip()
    cleaned = rewrite.strip()
    if not cleaned:
        return orig
    if any(token in orig and token not in cleaned for token in _REWRITE_CUE_TOKENS):
        return orig
    return cleaned


def sanitize_rewrite_query(rewrite: str, original: str) -> str:
    """过滤 meta 指令式改写，并保留原问中的时效/热度词。"""
    cleaned = rewrite.strip()
    orig = original.strip()
    if not cleaned:
        return orig
    if is_meta_rewrite(cleaned):
        return orig
    return restore_rewrite_cues(cleaned, orig)


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
    if label == "no_kb":
        # 模型不得直接输出 no_kb；有库时按 kb_search，未选库时才保留
        label = "kb_search" if kb_selected else "no_kb"
    if label == "image_only" and not has_images:
        label = "kb_search" if kb_selected else "no_kb"
    if label == "doc_only" and not has_attachments:
        label = "kb_search" if kb_selected else "no_kb"
    if not kb_selected and label in ("kb_search", "clarification", "summarize"):
        return "no_kb"
    return label


def needs_retrieval(intent: str | None, kb_selected: bool) -> bool:
    """是否需要知识库检索类工具（doc_retrieval / 图谱）。

    对齐 WeKnora NeedsKBRetrieval：kb_search / clarification / summarize；
    greeting / follow_up / image_only / doc_only / chitchat 不查库。
    未选知识库时不查库。web_search 走联网工具，不走知识库检索。
    """
    if not kb_selected:
        return False
    if not intent:
        return True
    return intent in RETRIEVAL_INTENTS


# 问候/闲聊/纯附件说明仍走一次 generate；其余在智能体绑了工具时进 ReAct
SKIP_REACT_INTENTS: frozenset[str] = frozenset(
    {"greeting", "chitchat", "image_only", "doc_only"}
)


def needs_agent_tools(
    intent: str | None,
    kb_selected: bool,
    *,
    web_search_enabled: bool | None = None,
    agent_has_tools: bool = False,
) -> bool:
    """是否进入 ReAct（可调工具）。问候/附件/图片/闲聊走一次 generate。

    智能体绑了工具时（PPT 等专业体），非 SKIP_REACT 意图一律进 ReAct。
    web_search 意图：输入框联网开启才走 ReAct；关闭时若已选知识库仍可检索库。
    """
    label = (intent or "").strip()
    if label in SKIP_REACT_INTENTS:
        return False
    if agent_has_tools:
        return True
    if label == "web_search":
        if web_search_enabled is None:
            from config.settings import settings

            web_search_enabled = bool(settings.web_search_enabled)
        if web_search_enabled:
            return True
        return bool(kb_selected)
    return needs_retrieval(intent, kb_selected)

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

# 给 LLM 的标签不含 no_kb（那是未选库时由 normalize_intent 生成的运行时标签）
LlmQueryIntent = Literal[
    "kb_search",
    "web_search",
    "follow_up",
    "greeting",
    "chitchat",
    "clarification",
    "summarize",
    "image_only",
    "doc_only",
]


class QueryUnderstandOutput(BaseModel):
    """query_understand 节点 LLM 输出。"""

    rewrite_query: str = Field(
        description="指代消解后的独立检索问句；须保留最近/比较火/最新等时效词"
    )
    intent: LlmQueryIntent = Field(description="用户意图分类，禁止输出 no_kb")
    image_description: str = Field(default="", description="图片分析/OCR 描述（有图时必填）")
