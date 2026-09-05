"""跨会话长期记忆：条件化 query_understand 改写 / 意图（对齐 WeKnora）。

读路径对齐 `RetrievalContextFor` + `memoryBackground`：
画像 / 兴趣（240 字预算）+ 常查资料标题 → `<asker_background>`，
只用于消解指代和补全检索词，不当作问题本身。

写路径先做 WeKnora 的 explicit_only：用户说「记住：…」立刻落库；
检索命中的文档累计亲和，hits≥2 才进入「常查资料」。
不接自动抽取、不把 MemoryPrompt 塞进最终回答（那是 MEMORY_RECALL）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from langchain_core.runnables import RunnableConfig

from config.settings import get_current_owner, settings

logger = logging.getLogger(__name__)

MEMORY_KIND_PROFILE = "profile"
MEMORY_KIND_INTEREST = "interest"
MEMORY_KIND_FACT = "fact"

MEMORY_CONTENT_MAX_RUNES = 300
RETRIEVAL_BACKGROUND_RUNE_BUDGET = 240
MEMORY_DOC_AFFINITY_MIN_HITS = 2
MEMORY_DOC_AFFINITY_TOP_N = 5

EXPLICIT_MEMORY_PREFIXES = (
    "记住：",
    "记住:",
    "记住，",
    "记住,",
    "记住 ",
    "记住",
    "请记住：",
    "请记住:",
    "请记住，",
    "请记住,",
    "请记住 ",
    "请记住",
    "帮我记住：",
    "帮我记住:",
    "帮我记住，",
    "帮我记住,",
    "帮我记住 ",
    "帮我记住",
    "remember that ",
    "remember: ",
    "remember, ",
    "please remember that ",
    "please remember: ",
    "note that ",
    "keep in mind that ",
)

_INTEREST_HINTS = ("关注", "感兴趣", "长期关注", "interested in", "focus on")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class RetrievalContext:
    """对齐 WeKnora RetrievalContext：进改写器，不进 UsedMemories。"""

    background: str = ""
    interests: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.background and not self.interests and not self.documents


def memory_enabled() -> bool:
    return bool(getattr(settings, "memory_enabled", True))


def retrieval_conditioning_enabled() -> bool:
    return memory_enabled() and bool(
        getattr(settings, "memory_retrieval_conditioning", True)
    )


def resolve_memory_owner(config: RunnableConfig | None = None) -> str:
    if config:
        raw = (config.get("configurable") or {}).get("owner")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return (get_current_owner() or settings.default_owner or "default").strip() or "default"


def sanitize_memory_content(content: str) -> str:
    text = (content or "").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = _CONTROL_RE.sub("", text)
    text = " ".join(text.split())
    runes = list(text)
    if len(runes) > MEMORY_CONTENT_MAX_RUNES:
        text = "".join(runes[:MEMORY_CONTENT_MAX_RUNES]).strip()
    return text


def detect_explicit_memory(query: str) -> str | None:
    """对齐 WeKnora DetectExplicitMemory：抽出「记住：…」后的陈述。"""
    trimmed = (query or "").strip()
    if not trimmed:
        return None
    lowered = trimmed.lower()
    for prefix in EXPLICIT_MEMORY_PREFIXES:
        if not lowered.startswith(prefix.lower()):
            continue
        statement = sanitize_memory_content(trimmed[len(prefix) :])
        statement = statement.lstrip("：:，, ")
        if len(statement) < 2:
            return None
        return statement
    return None


def infer_explicit_kind(content: str) -> str:
    """显式记忆默认当画像，这样才能进 asker_background。

    WeKnora 把「记住」写成 fact，留给回答侧 Recall；KnowSphere 暂不接
    MEMORY_RECALL，所以身份/背景类陈述写入 profile，含「关注」的写入 interest。
    """
    lowered = content.lower()
    if any(hint in lowered for hint in _INTEREST_HINTS):
        return MEMORY_KIND_INTEREST
    return MEMORY_KIND_PROFILE


def normalized_key(content: str) -> str:
    return sanitize_memory_content(content).lower()[:80]


def format_asker_background(ctx: RetrievalContext | None) -> str:
    """对齐 WeKnora memoryBackground 的精确标签格式。"""
    if ctx is None or ctx.empty():
        return ""
    parts = [
        "",
        "",
        '<asker_background note="背景仅用于消解指代和补全检索词，不要当作问题的一部分">',
    ]
    if ctx.background:
        parts.append(ctx.background)
    if ctx.interests:
        parts.append("长期关注：" + "、".join(ctx.interests))
    if ctx.documents:
        parts.append("常查资料：" + "、".join(ctx.documents))
    parts.append("</asker_background>")
    return "\n".join(parts)


def build_retrieval_context(
    items: Iterable[dict[str, Any]],
    documents: Iterable[str] | None = None,
    *,
    rune_budget: int = RETRIEVAL_BACKGROUND_RUNE_BUDGET,
) -> RetrievalContext:
    background: list[str] = []
    interests: list[str] = []
    budget = 0
    for item in items:
        if not item:
            continue
        line = sanitize_memory_content(str(item.get("content") or ""))
        if not line:
            continue
        cost = len(line) + 2
        if budget + cost > rune_budget:
            break
        budget += cost
        if str(item.get("kind") or "") == MEMORY_KIND_INTEREST:
            interests.append(line)
        else:
            background.append(line)
    docs: list[str] = []
    for title in documents or []:
        name = str(title or "").strip()
        if name and name not in docs:
            docs.append(name)
    return RetrievalContext(
        background="；".join(background),
        interests=interests,
        documents=docs,
    )


def retrieval_context_for(
    owner: str | None = None,
    config: RunnableConfig | None = None,
) -> RetrievalContext:
    if not retrieval_conditioning_enabled():
        return RetrievalContext()
    subject = (owner or resolve_memory_owner(config)).strip()
    if not subject:
        return RetrievalContext()
    try:
        from stores.memory_repository import MemoryStore

        store = MemoryStore()
        items = store.list_active_by_kinds(
            subject, [MEMORY_KIND_PROFILE, MEMORY_KIND_INTEREST], limit=30
        )
        doc_rows = store.top_doc_affinity(subject, limit=MEMORY_DOC_AFFINITY_TOP_N)
        titles = [
            str(row.get("title") or "").strip()
            for row in doc_rows
            if int(row.get("hits") or 0) >= MEMORY_DOC_AFFINITY_MIN_HITS
            and str(row.get("title") or "").strip()
        ]
        return build_retrieval_context(items, titles)
    except Exception as exc:
        logger.warning("加载长期记忆失败，跳过 asker_background: %s", exc)
        return RetrievalContext()


def remember_explicit(
    query: str,
    *,
    owner: str | None = None,
    config: RunnableConfig | None = None,
    session_id: str = "",
) -> dict[str, Any] | None:
    if not memory_enabled():
        return None
    statement = detect_explicit_memory(query)
    if not statement:
        return None
    subject = (owner or resolve_memory_owner(config)).strip()
    if not subject:
        return None
    kind = infer_explicit_kind(statement)
    try:
        from stores.memory_repository import MemoryStore

        return MemoryStore().upsert_item(
            owner=subject,
            kind=kind,
            content=statement,
            normalized_key=normalized_key(statement),
            origin="explicit",
            source_session_id=session_id,
        )
    except Exception as exc:
        logger.warning("写入显式记忆失败: %s", exc)
        return None


def record_answer_sources(
    sources: Iterable[dict[str, Any]] | None,
    *,
    owner: str | None = None,
    config: RunnableConfig | None = None,
) -> int:
    if not retrieval_conditioning_enabled() or not sources:
        return 0
    subject = (owner or resolve_memory_owner(config)).strip()
    if not subject:
        return 0
    seen: set[str] = set()
    recorded = 0
    try:
        from stores.memory_repository import MemoryStore

        store = MemoryStore()
        for item in sources:
            if not isinstance(item, dict):
                continue
            doc_id = str(item.get("document_id") or "").strip()
            title = str(item.get("file_name") or item.get("title") or "").strip()
            if not doc_id or doc_id in seen:
                continue
            if title.startswith("http://") or title.startswith("https://"):
                continue
            seen.add(doc_id)
            store.bump_doc_affinity(subject, doc_id, title)
            recorded += 1
    except Exception as exc:
        logger.warning("记录文档亲和失败: %s", exc)
        return recorded
    return recorded
