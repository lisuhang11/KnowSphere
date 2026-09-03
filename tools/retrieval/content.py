"""检索/精读正文截断。"""

from __future__ import annotations

import re

# 单次检索命中给模型的正文上限（父块回捞后）；引用卡片仍用 snippet
SEARCH_CONTENT_MAX = 4000
# list_chunks 单块正文上限
READ_CONTENT_MAX = 8000
LIST_CHUNKS_DEFAULT_LIMIT = 8
LIST_CHUNKS_MAX_LIMIT = 20
# grep_chunks：对齐 WeKnora（DB 最多捞 500，回模型 30；两侧上下文 200 字）
GREP_FETCH_LIMIT = 200
GREP_RETURN_LIMIT = 30
GREP_QUERY_MAX = 200
GREP_SNIPPET_CONTEXT = 200
GREP_SNIPPET_MATCH_MAX = 200
GREP_SNIPPET_TOTAL_MAX = 800
# get_document_info 列出知识库文档时的上限
LIST_DOCS_MAX = 50


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


def compile_grep_regex(query: str) -> re.Pattern[str] | None:
    """编译忽略大小写的 Python 正则，供片段定位；失败则返回 None。"""
    raw = (query or "").strip()
    if not raw:
        return None
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error:
        return None


def extract_match_snippet(content: str, compiled: re.Pattern[str] | None) -> str:
    """取最早匹配附近的一段上下文（对齐 WeKnora extractSnippetRegex）。"""
    text = content or ""
    if not text:
        return ""
    if compiled is None:
        return snippet_of(" ".join(text.split()), GREP_SNIPPET_TOTAL_MAX)

    match = compiled.search(text)
    if match is None:
        return snippet_of(" ".join(text.split()), GREP_SNIPPET_TOTAL_MAX)

    start, end = match.start(), match.end()
    before = text[:start]
    matched = text[start:end]
    after = text[end:]
    if len(before) > GREP_SNIPPET_CONTEXT:
        before = before[-GREP_SNIPPET_CONTEXT:]
    if len(after) > GREP_SNIPPET_CONTEXT:
        after = after[:GREP_SNIPPET_CONTEXT]
    if len(matched) > GREP_SNIPPET_MATCH_MAX:
        matched = matched[:GREP_SNIPPET_MATCH_MAX] + "…"
    snippet = " ".join((before + matched + after).split())
    if len(snippet) > GREP_SNIPPET_TOTAL_MAX:
        snippet = snippet[:GREP_SNIPPET_TOTAL_MAX] + "…"
    return f"… {snippet} …"
