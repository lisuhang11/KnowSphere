"""检索后父块扩展：命中子块时回捞父块内容。"""

from __future__ import annotations

from typing import Any

from utils.vector_store import ChunkStore

SNIPPET_LEN = 300


def join_chunk_content(parent: str, child: str, sep: str = "\n\n") -> str:
    """拼接父块与子块；子块已含于父块时只返回父块。"""
    parent = parent.strip()
    child = child.strip()
    if not parent:
        return child
    if not child or child in parent:
        return parent
    return f"{parent}{sep}{child}"


def snippet_from_resolved(full: str, child: str, max_len: int = SNIPPET_LEN) -> str:
    """扩展后 snippet：优先从子块对应位置截取。"""
    if not full:
        return ""
    needle = child.strip()
    if needle:
        idx = full.find(needle)
        if idx >= 0:
            start = max(0, idx - 50)
            return full[start : start + max_len]
    return full[:max_len]


def resolve_parent_chunks(
    rows: list[dict[str, Any]],
    store: ChunkStore,
) -> list[dict[str, Any]]:
    """批量拉父块并扩展 content/snippet。"""
    if not rows:
        return rows

    parent_ids = {
        int(r["parent_chunk_id"])
        for r in rows
        if r.get("parent_chunk_id") is not None
    }
    if not parent_ids:
        return rows

    parents = store.get_chunks_by_ids(list(parent_ids))
    parent_map = {p["id"]: p for p in parents}

    for row in rows:
        pid = row.get("parent_chunk_id")
        if pid is None:
            continue
        parent = parent_map.get(int(pid))
        if not parent or not parent.get("content"):
            continue
        if parent.get("chunk_type") != "parent_text":
            continue
        child_content = row.get("content") or ""
        merged = join_chunk_content(parent["content"], child_content)
        row["content"] = merged
        row["snippet"] = snippet_from_resolved(merged, child_content)
        row["parent_resolved"] = True
        row["sub_chunk_index"] = row.get("chunk_index")
    return rows
