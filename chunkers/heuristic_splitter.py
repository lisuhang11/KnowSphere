"""Tier2 heuristic splitter：按启发式结构边界切分（无 Markdown 标题时的中间档）。

启发式文本分块：
- 探测结构边界：章节标记、分页符(\f)、编号章节、全大写伪标题行、视觉分隔符
- 代码块/表格等"受保护区域"内的行不产生边界
- 按边界切 segment，超长 segment 内部用 legacy splitter 二次切分
- 无任何结构边界返回 None（交由上层降级到 legacy）
"""

from __future__ import annotations

from chunkers.legacy_splitter import split_recursive
from chunkers.profiler import (
    ALL_CAPS_LINE_RE,
    CHAPTER_MARKER_RE,
    CODE_FENCE_RE,
    NUMBERED_SECTION_RE,
    TABLE_LINE_RE,
    VISUAL_SEP_RE,
)

# 边界优先级：章节标记 > 分页符 > 编号章节 > 伪标题 > 视觉分隔符
_BOUNDARY_PRIORITY: list[tuple[int, object]] = [
    (10, CHAPTER_MARKER_RE),
    (9, None),  # form feed，占位
    (7, NUMBERED_SECTION_RE),
    (5, ALL_CAPS_LINE_RE),
    (3, VISUAL_SEP_RE),
]

def _detect_boundaries(lines: list[str]) -> list[bool]:
    """逐行探测结构边界。返回与行等长的布尔列表（True = 新块起始）。"""
    boundaries = [False] * len(lines)
    in_code = False
    table_streak = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            continue
        if in_code or not stripped:
            table_streak = 0
            continue
        if TABLE_LINE_RE.match(line):
            table_streak += 1
            if table_streak >= 2:
                continue  # 表格块内不产生边界
        else:
            table_streak = 0

        score = 0
        if "\f" in stripped:
            score = 9
        else:
            for priority, pattern in _BOUNDARY_PRIORITY:
                if pattern is None:
                    continue
                if pattern.match(line):
                    score = priority
                    break
        if score:
            boundaries[i] = True
    return boundaries

def split_by_heuristic(
    text: str, chunk_size: int, chunk_overlap: int
) -> list[dict] | None:
    """按启发式边界切分。返回 [{'content','context_header':None}] 列表；无边界时返回 None。"""
    lines = text.split("\n")
    boundaries = _detect_boundaries(lines)
    if not any(boundaries):
        return None

    # 按边界切 segment：边界行作为新 segment 的起始
    segments: list[str] = []
    current: list[str] = []
    for i, line in enumerate(lines):
        if boundaries[i] and current:
            segments.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        segments.append("\n".join(current))

    # 贪心打包
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        if len(seg) > chunk_size:
            if buf:
                chunks.append("\n".join(buf))
                buf, buf_len = [], 0
            for piece in split_recursive(seg, chunk_size, chunk_overlap):
                if piece.strip():
                    chunks.append(piece)
        else:
            if buf and buf_len + len(seg) > chunk_size:
                chunks.append("\n".join(buf))
                buf, buf_len = [], 0
            buf.append(seg)
            buf_len += len(seg)
    if buf:
        chunks.append("\n".join(buf))

    if not chunks:
        return None
    return [{"content": c, "context_header": None} for c in chunks]
