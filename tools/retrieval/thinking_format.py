"""doc_retrieval thinking 文案格式化。"""

from __future__ import annotations

from typing import Any


def format_recall_start(
    query: str,
    queries: list[str],
    mq_subs: list[str],
    recall_k: int,
    expansion_threshold: int,
) -> str:
    """首轮检索开场（默认单路；mq_subs 仅兼容旧调用）。"""
    if mq_subs:
        mode = f"LLM 多跳（原 query + {len(mq_subs)} 条子查询）"
    else:
        mode = "单路（改写后的主检索词）"
    lines = [
        f"【2/5 首轮检索】模式：{mode}，候选池上限 {recall_k}，扩展阈值 {expansion_threshold}",
        f"主检索词：{query}",
        f"实际检索 {len(queries)} 路：",
    ]
    lines.extend(f"  - {q}" for q in queries)
    return "\n".join(lines)


def format_recall_result(count: int, recall_k: int, threshold: int) -> str:
    if count >= threshold:
        hint = f"≥ 扩展阈值 {threshold}，跳过多跳 / 本地扩展"
    elif count == 0:
        hint = f"< 扩展阈值 {threshold}，且无命中，将按需尝试多跳 / 本地扩展"
    else:
        hint = f"< 扩展阈值 {threshold}，命中偏少，将按需尝试多跳 / 本地扩展"
    return f"【2/5 首轮结果】融合后 {count} 条候选（池上限 {recall_k}）→ {hint}"


def format_multi_query_trigger(
    count: int,
    threshold: int,
    mq_subs: list[str],
) -> str:
    lines = [
        f"【3/5 多跳增强】首轮单路仅 {count} 条候选 < 阈值 {threshold}，"
        f"因此触发 LLM 多跳（生成 {len(mq_subs)} 条子查询补搜）：",
    ]
    lines.extend(f"  - {q}" for q in mq_subs)
    return "\n".join(lines)


def format_multi_query_result(mq_raw: int, before: int, after: int) -> str:
    return (
        f"多跳补搜 raw 命中 {mq_raw} 条；"
        f"与首轮 {before} 条 RRF 合并 → 候选池 {after} 条"
    )


def format_expansion_result(
    variants: list[str],
    exp_raw: int,
    before: int,
    after: int,
) -> str:
    lines = [
        f"【3/5 本地扩展】候选仍 < 阈值，追加 {len(variants)} 路变体（规则生成，无 LLM）：",
    ]
    lines.extend(f"  - {v}" for v in variants)
    lines.append(
        f"扩展路 raw 命中 {exp_raw} 条；与当前 {before} 条 RRF 合并 → 候选池 {after} 条"
    )
    return "\n".join(lines)


def format_source_preview(rows: list[dict[str, Any]], limit: int = 5) -> str:
    lines: list[str] = []
    for i, r in enumerate(rows[:limit], 1):
        name = r.get("file_name") or r.get("document_id") or "?"
        idx = r.get("chunk_index", "?")
        score = float(r.get("score") or 0)
        snippet = (r.get("snippet") or r.get("content") or "")[:60].replace("\n", " ")
        lines.append(f"  [{i}] {name}#{idx} score={score:.3f} | {snippet}…")
    if len(rows) > limit:
        lines.append(f"  … 另有 {len(rows) - limit} 条未展示")
    return "\n".join(lines)
