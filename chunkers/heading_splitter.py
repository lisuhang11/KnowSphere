"""Tier1 heading splitter：按 Markdown 标题层级切分，带面包屑上下文。

按标题层级分块：
- 找出"主导标题层级"（出现≥3次的最浅层，否则最深层）
- 按主导层级把文档切成 section，每个 section 携带祖先标题链（面包屑）
- section 超长时内部用 legacy splitter 二次切分，子块保留面包屑
- 相邻共享标题的小块自动合并（coalesce）
- 无标题结构返回 None（交由上层降级到 heuristic / legacy）
"""

from __future__ import annotations

from collections import Counter

from chunkers.legacy_splitter import split_recursive
from chunkers.profiler import MD_HEADING_RE, _compute_dominant_level

# 面包屑里每层标题的前缀：展示用 markdown 形式
_HEADING_PREFIX = "#"

def _build_breadcrumb(
    headings: list[tuple[int, str, int]], lineno: int, dom: int, title: str
) -> str:
    """构建该主导标题的祖先链（最近祖先每层取一个），如 "# 第一章\\n## 1.2 小节"。"""
    crumbs: list[str] = []
    for level in range(1, dom):
        best: tuple[int, str, int] | None = None
        for lv, t, ln in headings:
            if lv == level and ln < lineno and (best is None or ln > best[2]):
                best = (lv, t, ln)
        if best is not None:
            crumbs.append(_HEADING_PREFIX * level + " " + best[1])
    crumbs.append(_HEADING_PREFIX * dom + " " + title)
    return "\n".join(crumbs)

def split_by_heading(
    text: str, chunk_size: int, chunk_overlap: int
) -> list[dict] | None:
    """按标题层级切分。返回 [{'content','context_header'}] 列表；无标题结构时返回 None。"""
    lines = text.split("\n")
    headings: list[tuple[int, str, int]] = []
    for lineno, line in enumerate(lines):
        m = MD_HEADING_RE.match(line)
        if m:
            headings.append((len(m.group(1)), m.group(2).strip(), lineno))
    if not headings:
        return None

    counts = Counter(level for level, _title, _ln in headings)
    dom = _compute_dominant_level(counts)

    # 按主导层级切 section：[start_line, end_line] 闭区间 + 面包屑
    dom_heading_idx = [i for i, (lv, _t, _ln) in enumerate(headings) if lv == dom]
    sections: list[tuple[int, int, str | None]] = []
    for idx_pos, h_idx in enumerate(dom_heading_idx):
        level, title, lineno = headings[h_idx]
        end_line = (
            headings[dom_heading_idx[idx_pos + 1]][2] - 1
            if idx_pos + 1 < len(dom_heading_idx)
            else len(lines) - 1
        )
        breadcrumb = _build_breadcrumb(headings, lineno, dom, title)
        sections.append((lineno, end_line, breadcrumb))

    # 文档开头的前言（第一个主导标题之前）
    preamble_end = sections[0][0] - 1
    if preamble_end >= 0 and "\n".join(lines[: preamble_end + 1]).strip():
        sections.insert(0, (0, preamble_end, None))

    chunks: list[dict] = []
    for start, end, breadcrumb in sections:
        content = "\n".join(lines[start : end + 1]).strip()
        if not content:
            continue
        if len(content) <= chunk_size:
            chunks.append({"content": content, "context_header": breadcrumb})
        else:
            for piece in split_recursive(content, chunk_size, chunk_overlap):
                if piece.strip():
                    chunks.append({"content": piece, "context_header": breadcrumb})

    if not chunks:
        return None
    return _coalesce(chunks, chunk_size)

def _coalesce(chunks: list[dict], chunk_size: int) -> list[dict]:
    """合并相邻、共享相同面包屑、且合并后不超限的小块。"""
    merged: list[dict] = []
    for chunk in chunks:
        if (
            merged
            and merged[-1]["context_header"] == chunk["context_header"]
            and merged[-1]["context_header"] is not None
            and len(merged[-1]["content"]) + len(chunk["content"]) <= chunk_size
        ):
            merged[-1]["content"] += "\n" + chunk["content"]
        else:
            merged.append(dict(chunk))
    return merged
