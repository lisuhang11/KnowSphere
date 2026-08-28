"""临时附件分块与按 query 选段。"""

from __future__ import annotations

import json
import re
from typing import Any

ATTACHMENT_PROMPT_BUDGET_CHARS = 12_000
ATTACHMENT_INLINE_CHARS = 4_000
ATTACHMENT_CHUNK_SIZE = 1600
ATTACHMENT_CHUNK_OVERLAP = 160
MAX_CHUNKS_IN_PROMPT = 20

_TERM_SPLIT_RE = re.compile(r"[\s，。！？、；：\[\]【】\-_/\\]+")

def approx_token_count(text: str) -> int:
    """粗估 token 数（中文约 1 字 1 token，英文按词）。"""
    if not text:
        return 0
    return max(1, len(text) // 2)

def split_attachment_chunks(content: str) -> list[dict[str, Any]]:
    """将附件全文切分为带 seq 的 chunk 列表。"""
    text = (content or "").strip()
    if not text:
        return []
    if len(text) <= ATTACHMENT_INLINE_CHARS:
        return [
            {
                "seq": 0,
                "content": text,
                "context_header": "",
                "token_count": approx_token_count(text),
            }
        ]

    chunks: list[dict[str, Any]] = []
    start = 0
    seq = 0
    step = max(1, ATTACHMENT_CHUNK_SIZE - ATTACHMENT_CHUNK_OVERLAP)
    while start < len(text):
        end = min(len(text), start + ATTACHMENT_CHUNK_SIZE)
        part = text[start:end].strip()
        if part:
            chunks.append(
                {
                    "seq": seq,
                    "content": part,
                    "context_header": f"[片段 {seq + 1}]" if seq else "",
                    "token_count": approx_token_count(part),
                }
            )
            seq += 1
        if end >= len(text):
            break
        start += step
    return chunks

def query_terms(query: str) -> list[str]:
    """提取 query 检索词（小写、去重）。"""
    raw = _TERM_SPLIT_RE.split((query or "").lower())
    seen: set[str] = set()
    terms: list[str] = []
    for term in raw:
        t = term.strip()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        terms.append(t)
    return terms

def _chunks_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("chunks")
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    content = (row.get("content") or "").strip()
    if content:
        return split_attachment_chunks(content)
    return []

def select_attachment_content(
    row: dict[str, Any],
    query: str,
    *,
    budget_chars: int | None = None,
) -> tuple[str, int, int]:
    """按 query 相关性从 chunks 选段，返回 (content, selected_count, total_count)。"""
    chunks = _chunks_from_row(row)
    full = (row.get("content") or "").strip()
    total = len(chunks)
    budget = budget_chars if budget_chars is not None else ATTACHMENT_PROMPT_BUDGET_CHARS

    if not chunks:
        body = full or (row.get("image_description") or "").strip()
        return body, 0, 0

    full_tokens = approx_token_count(full)
    if total <= 1 or (full_tokens * 2 <= budget and len(full) <= ATTACHMENT_INLINE_CHARS):
        return full, total, total

    terms = query_terms(query)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for part in chunks:
        text = ((part.get("context_header") or "") + "\n" + (part.get("content") or "")).lower()
        score = 0
        for term in terms:
            score += text.count(term) * (1 + len(term) // 2)
        ranked.append((score, part))

    ranked.sort(key=lambda x: (-x[0], int(x[1].get("seq") or 0)))

    selected: list[dict[str, Any]] = []
    used = 0
    for _score, part in ranked:
        if len(selected) >= MAX_CHUNKS_IN_PROMPT:
            break
        piece = (part.get("content") or "").strip()
        if not piece:
            continue
        piece_len = len(piece)
        if used > 0 and used + piece_len > budget:
            continue
        selected.append(part)
        used += piece_len

    if not selected:
        selected = [ranked[0][1]] if ranked else chunks[:1]

    selected.sort(key=lambda p: int(p.get("seq") or 0))
    body = "\n\n".join(
        f"{(p.get('context_header') or '').strip()}\n{p.get('content') or ''}".strip()
        for p in selected
    ).strip()
    return body or full, len(selected), total
