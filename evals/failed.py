"""评测失败题判定：运行出错，或 RAG 收集阶段空答。"""

from __future__ import annotations

from typing import Any


def is_retryable_sample(sample: dict[str, Any], *, suite: str) -> bool:
    if str(sample.get("error") or "").strip():
        return True
    if suite == "rag_quality" and not str(sample.get("response") or "").strip():
        return True
    return False


def retryable_qids(samples: list[dict[str, Any]], *, suite: str) -> list[int]:
    qids: list[int] = []
    seen: set[int] = set()
    for sample in samples:
        if not is_retryable_sample(sample, suite=suite):
            continue
        qid = int(sample.get("qid") or 0)
        if qid in seen:
            continue
        seen.add(qid)
        qids.append(qid)
    return qids
