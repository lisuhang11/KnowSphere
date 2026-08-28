"""RRF 多路检索融合（纯函数）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

def rrf_fuse(
    ranked: Sequence[Sequence[dict[str, Any]]],
    k: int,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """多路检索结果按 rank 做 RRF 融合，取前 k。"""
    fused: dict[tuple[str, int], dict[str, Any]] = {}
    for hits in ranked:
        for rank, hit in enumerate(hits):
            key = (hit["document_id"], hit["chunk_index"])
            entry = fused.setdefault(key, dict(hit))
            for field, value in hit.items():
                if field != "rrf" and entry.get(field) is None:
                    entry[field] = value
            entry.setdefault("rrf", 0.0)
            entry["rrf"] += 1.0 / (rrf_k + rank + 1)
    picked = sorted(fused.values(), key=lambda e: e["rrf"], reverse=True)[:k]
    for e in picked:
        e["score"] = round(e["rrf"], 4)
    return picked
