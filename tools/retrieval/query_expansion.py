"""本地 query expansion（无 LLM 扩展）。"""

from __future__ import annotations

import re

# 中英文停用词（精简集）
_STOPWORDS = frozenset(
    {
        "的", "是", "在", "了", "和", "与", "或", "a", "an", "the", "is", "are",
        "what", "how", "why", "when", "where", "which", "who",
        "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    }
)

_QUESTION_PREFIX = re.compile(
    r"^(什么是|什么|如何|怎么|怎样|为什么|为何|哪个|哪些|谁|何时|何地|请问|请告诉我|帮我|我想知道|我想了解)"
)

_QUOTED = re.compile(r'["\'""「」『』]([^"\'""「」『』]+)["\'""「」『』]')
_DELIMS = re.compile(r"[,，;；、。！？!?\s]+")


def expand_queries_local(query: str, max_variants: int = 5) -> list[str]:
    """从主 query 生成 ≤max_variants 个检索变体（不含原 query）。"""
    query = (query or "").strip()
    if not query or max_variants <= 0:
        return []

    seen: set[str] = {query.lower()}
    expansions: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if len(s) < 3:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        expansions.append(s)

    keywords = _extract_keywords(query)
    if len(keywords) >= 2:
        add(" ".join(keywords))

    for phrase in _QUOTED.findall(query):
        if len(phrase.strip()) > 2:
            add(phrase.strip())

    for seg in _DELIMS.split(query):
        seg = seg.strip()
        if len(seg) > 5:
            add(seg)

    cleaned = _QUESTION_PREFIX.sub("", query).strip()
    if cleaned and cleaned != query:
        add(cleaned)

    return expansions[:max_variants]


def _extract_keywords(text: str) -> list[str]:
    tokens = _tokenize(text)
    out: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        if len(tok) <= 1 and not _is_cjk(tok):
            continue
        out.append(tok)
    return out


def _is_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    buf: list[str] = []
    buf_is_cjk = False

    def flush() -> None:
        nonlocal buf, buf_is_cjk
        if not buf:
            return
        if buf_is_cjk:
            tokens.extend(buf)
        else:
            tokens.append("".join(buf))
        buf = []

    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            if buf and not buf_is_cjk:
                flush()
            buf_is_cjk = True
            buf.append(ch)
        elif ch.isalnum():
            if buf and buf_is_cjk:
                flush()
            buf_is_cjk = False
            buf.append(ch)
        else:
            flush()
    flush()
    return tokens
