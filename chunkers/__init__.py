"""自适应分块器门面：多策略切分 + 校验降级 + 预览诊断。

切块策略注册表：
- 四种策略：auto（自适应）/ heading / heuristic / recursive(=legacy 别名)
- auto 模式通过文档画像（profiler）选择 tier 链
- 依次执行 tier，输出经校验（ValidateChunks）不通过则记录原因并降级到下一档
- legacy 永不失败，作为最终兜底
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings
from chunkers.legacy_splitter import split_recursive
from chunkers.heading_splitter import split_by_heading
from chunkers.heuristic_splitter import split_by_heuristic
from chunkers.profiler import DocumentProfile, profile_document, select_strategy

VALID_STRATEGIES = ("auto", "heading", "heuristic", "recursive")
# 对外别名 → 内部 tier 名
_ALIASES = {"recursive": "legacy"}

# 最小 chunk 阈值（避免把内容切得过于细碎）：小于该值视为 tiny
_TINY_RATIO = 0.2
# 超大 chunk 判定倍数
_OVERSIZE_FACTOR = 2.0

@dataclass
class SplitChunk:
    content: str
    context_header: str | None = None

@dataclass
class SplitResult:
    strategy: str
    selected_tier: str
    tier_chain: list[str]
    rejected: list[dict[str, str]]
    profile: dict[str, Any]
    stats: dict[str, Any]
    chunk_size: int
    chunk_overlap: int
    chunks: list[SplitChunk]

def _validate(chunks: list[dict], chunk_size: int, full_text: str) -> tuple[bool, str]:
    """校验切块输出。返回 (ok, reason)。"""
    if not chunks:
        return False, "切块结果为空"
    # 完整度：切块拼接后不应丢失大部分原文
    joined = "".join(c["content"] for c in chunks).strip()
    if joined and len(joined) < 0.5 * len(full_text.strip()):
        return False, "切块结果缺失大量原文内容"
    # 过多极小 chunk 视为过度切分
    tiny = sum(1 for c in chunks if len(c["content"]) < max(8, int(chunk_size * _TINY_RATIO)))
    if len(chunks) > 5 and tiny / len(chunks) > 0.5:
        return False, f"产生过多过小分块（{tiny}/{len(chunks)}）"
    # 过多超长 chunk（超过 2 倍 chunk_size）
    oversized = sum(1 for c in chunks if len(c["content"]) > chunk_size * _OVERSIZE_FACTOR)
    if oversized > max(1, len(chunks) // 4):
        return False, f"存在过多超长分块（{oversized}）"
    return True, ""

def _compute_stats(chunks: list[dict]) -> dict[str, Any]:
    lens = [len(c["content"]) for c in chunks]
    n = len(lens)
    if n == 0:
        return {"chunk_count": 0, "avg_chars": 0, "stddev_chars": 0, "min_chars": 0, "max_chars": 0}
    avg = sum(lens) / n
    stddev = statistics.pstdev(lens) if n > 1 else 0.0
    return {
        "chunk_count": n,
        "avg_chars": round(avg, 1),
        "stddev_chars": round(stddev, 1),
        "min_chars": min(lens),
        "max_chars": max(lens),
    }

def _run_tier(
    tier: str, text: str, chunk_size: int, chunk_overlap: int
) -> list[dict] | None:
    """执行单个 tier。返回 [{content, context_header}]；结构信号不足返回 None。"""
    if tier == "heading":
        return split_by_heading(text, chunk_size, chunk_overlap)
    if tier == "heuristic":
        return split_by_heuristic(text, chunk_size, chunk_overlap)
    return [
        {"content": piece, "context_header": None}
        for piece in split_recursive(text, chunk_size, chunk_overlap)
    ]

def split_with_diagnostics(
    text: str,
    strategy: str = "auto",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> SplitResult:
    """自适应切块 + 诊断。strategy ∈ auto/heading/heuristic/recursive。"""
    chunk_size = chunk_size or settings.chunk_size
    chunk_overlap = (
        chunk_overlap if chunk_overlap is not None else settings.chunk_overlap
    )
    if strategy not in VALID_STRATEGIES:
        raise ValueError(f"未知切块策略: {strategy}（可选: {', '.join(VALID_STRATEGIES)}）")

    profile = profile_document(text)

    if strategy == "auto":
        tier_chain, _reason = select_strategy(profile)
    elif strategy == "heading":
        tier_chain = ["heading", "legacy"]
    elif strategy == "heuristic":
        tier_chain = ["heuristic", "legacy"]
    else:
        tier_chain = ["legacy"]

    rejected: list[dict[str, str]] = []
    chunks: list[dict] = []
    selected_tier = "legacy"
    for tier in tier_chain:
        result = _run_tier(tier, text, chunk_size, chunk_overlap)
        if result is None:
            rejected.append({"tier": tier, "reason": "结构信号不足，无法应用该策略"})
            continue
        ok, reason = _validate(result, chunk_size, text)
        if not ok:
            rejected.append({"tier": tier, "reason": reason})
            continue
        chunks, selected_tier = result, tier
        break

    # 兜底：legacy 恒可用（除非文本为空）
    if not chunks:
        chunks = [
            {"content": piece, "context_header": None}
            for piece in split_recursive(text, chunk_size, chunk_overlap)
        ]
        selected_tier = "legacy"

    return SplitResult(
        strategy=strategy,
        selected_tier=selected_tier,
        tier_chain=tier_chain,
        rejected=rejected,
        profile=profile.to_dict,
        stats=_compute_stats(chunks),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunks=[SplitChunk(**c) for c in chunks],
    )

def split_plain(
    text: str,
    strategy: str = "auto",
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[str]:
    """便捷入口：只返回文本块列表（摄取用）。"""
    result = split_with_diagnostics(text, strategy, chunk_size, chunk_overlap)
    return [c.content for c in result.chunks]

from chunkers.parent_child import (  # noqa: E402
    ParentChildResult,
    ParentChunk,
    ChildChunk,
    split_parent_child_with_diagnostics,
    embedding_content,
    derive_parent_child_configs,
    merge_breadcrumbs,
)

__all__ = [
    "VALID_STRATEGIES",
    "SplitChunk",
    "SplitResult",
    "ParentChildResult",
    "ParentChunk",
    "ChildChunk",
    "split_with_diagnostics",
    "split_parent_child_with_diagnostics",
    "split_plain",
    "embedding_content",
    "derive_parent_child_configs",
    "merge_breadcrumbs",
    "profile_document",
    "DocumentProfile",
]
