"""父子分块：大父块存上下文，小子块用于向量检索。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass
class ParentChunk:
    content: str
    context_header: str | None = None

@dataclass
class ChildChunk:
    content: str
    context_header: str | None = None
    parent_index: int = -1  # -1 = 无冗余父块

@dataclass
class ParentChildResult:
    strategy: str
    selected_tier: str
    tier_chain: list[str]
    rejected: list[dict[str, str]]
    profile: dict[str, Any]
    stats: dict[str, Any]
    parent_chunk_size: int
    child_chunk_size: int
    chunk_overlap: int
    parents: list[ParentChunk]
    children: list[ChildChunk]

def derive_parent_child_configs(
    *,
    strategy: str,
    chunk_overlap: int,
    parent_size: int = 4096,
    child_size: int = 384,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """推导父/子切分配置。子块 overlap 固定为 child_size // 5。"""
    if parent_size <= 0:
        parent_size = 4096
    if child_size <= 0:
        child_size = 384
    parent_cfg = {
        "strategy": strategy,
        "chunk_size": parent_size,
        "chunk_overlap": chunk_overlap,
    }
    child_cfg = {
        "strategy": strategy,
        "chunk_size": child_size,
        "chunk_overlap": max(1, child_size // 5),
    }
    return parent_cfg, child_cfg

def merge_breadcrumbs(parent: str | None, child: str | None) -> str | None:
    """合并父/子标题面包屑，去掉子块首行与父块末行重复。"""
    if not parent:
        return child
    if not child:
        return parent
    parent_lines = parent.split("\n")
    child_lines = child.split("\n")
    if (
        parent_lines
        and child_lines
        and parent_lines[-1].strip() == child_lines[0].strip()
    ):
        child_lines = child_lines[1:]
    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)

def embedding_content(content: str, context_header: str | None) -> str:
    """子块 embedding 文本：面包屑 + 正文。"""
    body = content.strip()
    if not context_header:
        return body
    return f"{context_header.strip()}\n\n{body}"

def split_parent_child_with_diagnostics(
    text: str,
    *,
    strategy: str = "auto",
    parent_size: int = 4096,
    child_size: int = 384,
    chunk_overlap: int = 90,
) -> ParentChildResult:
    """两级切分：先父后子；诊断信息来自父块那一趟切分。"""
    from chunkers import split_with_diagnostics  # 延迟导入，避免 __init__ 循环依赖

    parent_cfg, child_cfg = derive_parent_child_configs(
        strategy=strategy,
        chunk_overlap=chunk_overlap,
        parent_size=parent_size,
        child_size=child_size,
    )
    parent_result = split_with_diagnostics(
        text,
        strategy=parent_cfg["strategy"],
        chunk_size=parent_cfg["chunk_size"],
        chunk_overlap=parent_cfg["chunk_overlap"],
    )
    if not parent_result.chunks:
        return ParentChildResult(
            strategy=strategy,
            selected_tier=parent_result.selected_tier,
            tier_chain=parent_result.tier_chain,
            rejected=parent_result.rejected,
            profile=parent_result.profile,
            stats={"chunk_count": 0, "parent_count": 0, "child_count": 0},
            parent_chunk_size=parent_cfg["chunk_size"],
            child_chunk_size=child_cfg["chunk_size"],
            chunk_overlap=chunk_overlap,
            parents=[],
            children=[],
        )

    stored_parents: list[ParentChunk] = []
    children: list[ChildChunk] = []
    for parent in parent_result.chunks:
        child_result = split_with_diagnostics(
            parent.content,
            strategy=child_cfg["strategy"],
            chunk_size=child_cfg["chunk_size"],
            chunk_overlap=child_cfg["chunk_overlap"],
        )
        subs = child_result.chunks
        parent_index = -1
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(stored_parents)
            stored_parents.append(
                ParentChunk(content=parent.content, context_header=parent.context_header)
            )
        for sub in subs:
            children.append(
                ChildChunk(
                    content=sub.content,
                    context_header=merge_breadcrumbs(parent.context_header, sub.context_header),
                    parent_index=parent_index,
                )
            )

    child_lens = [len(c.content) for c in children]
    stats: dict[str, Any] = {
        "parent_count": len(stored_parents),
        "child_count": len(children),
        "chunk_count": len(children),
    }
    if child_lens:
        stats.update(
            {
                "avg_chars": round(sum(child_lens) / len(child_lens), 1),
                "min_chars": min(child_lens),
                "max_chars": max(child_lens),
            }
        )

    return ParentChildResult(
        strategy=strategy,
        selected_tier=parent_result.selected_tier,
        tier_chain=parent_result.tier_chain,
        rejected=parent_result.rejected,
        profile=parent_result.profile,
        stats=stats,
        parent_chunk_size=parent_cfg["chunk_size"],
        child_chunk_size=child_cfg["chunk_size"],
        chunk_overlap=chunk_overlap,
        parents=stored_parents,
        children=children,
    )
