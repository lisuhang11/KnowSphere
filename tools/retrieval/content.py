"""检索/精读正文截断。"""

from __future__ import annotations

# 单次检索命中给模型的正文上限（父块回捞后）；引用卡片仍用 snippet
SEARCH_CONTENT_MAX = 4000
# list_chunks 单块正文上限
READ_CONTENT_MAX = 8000
LIST_CHUNKS_DEFAULT_LIMIT = 8
LIST_CHUNKS_MAX_LIMIT = 20


def clip_content(text: str, max_chars: int) -> str:
    cleaned = (text or "").strip()
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "…"


def snippet_of(text: str, max_len: int = 300) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[:max_len]
