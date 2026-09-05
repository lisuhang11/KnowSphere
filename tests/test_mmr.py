"""MMR：向量 to_list / min/max/mean 必须调用，不能把方法对象当数值。"""

from __future__ import annotations

from unittest.mock import MagicMock

from services.retrieval_service import RetrievalService


class _Vec:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def to_list(self) -> list[float]:
        return list(self._values)


def _row(i: int, emb: object, score: float = 0.9) -> dict:
    return {
        "document_id": f"d{i}",
        "score": score,
        "content": f"chunk-{i} " * 4,
        "embedding": emb,
    }


def test_mmr_select_accepts_to_list_embeddings():
    svc = RetrievalService(store=MagicMock())
    rows = [_row(i, _Vec([float(i), 1.0, 0.0]), score=1.0 - i * 0.05) for i in range(5)]
    picked = svc._mmr_select(rows, k=3)
    assert len(picked) == 3
    assert {r["document_id"] for r in picked} <= {r["document_id"] for r in rows}


def test_mmr_select_accepts_plain_list_embeddings():
    svc = RetrievalService(store=MagicMock())
    rows = [_row(i, [float(i), 0.5, 0.1], score=0.8 - i * 0.1) for i in range(4)]
    picked = svc._mmr_select(rows, k=2)
    assert len(picked) == 2
