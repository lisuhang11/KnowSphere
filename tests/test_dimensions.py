"""回归：embedding 维度解析与 pgvector HNSW 上限校验（P1）。

覆盖历史 bug：Qwen/Qwen3-Embedding-4B 输出 2560 维，pgvector HNSW 上限 2000，
此前无校验直接建索引 → SQL 报错且提示难懂。
"""

from __future__ import annotations

import pytest

from models import dimensions
from models.dimensions import MAX_HNSW_DIM, resolve_embedding_dim

class _FakeEmbeddings:
    def __init__(self, dim: int, fail: bool = False):
        self._dim = dim
        self._fail = fail

    def embed_query(self, text: str):
        if self._fail:
            raise RuntimeError("模拟 embedding API 调用失败")
        return [0.0] * self._dim

def _patch_embed(monkeypatch, dim: int | None = None, fail: bool = False):
    monkeypatch.setattr(
        dimensions, "create_embeddings", lambda model=None: _FakeEmbeddings(dim, fail)
    )

def test_registry_dim_hit():
    assert resolve_embedding_dim("BAAI/bge-m3") == 1024

def test_unknown_model_probed(monkeypatch):
    _patch_embed(monkeypatch, dim=512)
    assert resolve_embedding_dim("fake/model-512") == 512

def test_probe_failure_raises(monkeypatch):
    _patch_embed(monkeypatch, fail=True)
    with pytest.raises(ValueError, match="维度"):
        resolve_embedding_dim("fake/model-broken")

def test_over_limit_raises_with_hint(monkeypatch):
    _patch_embed(monkeypatch, dim=MAX_HNSW_DIM + 560)  # 如 Qwen3-Embedding-4B 的 2560
    with pytest.raises(ValueError, match=str(MAX_HNSW_DIM)):
        resolve_embedding_dim("fake/model-2560")

def test_over_limit_matches_real_registry_entry():
    """注册表中确有超上限模型时，直接解析也应报同样错误。"""
    model_id = next(
        (m for m, d in dimensions.EMBEDDING_DIMENSIONS.items() if d > MAX_HNSW_DIM),
        None,
    )
    if model_id is None:
        pytest.skip("注册表中无超上限模型")
    with pytest.raises(ValueError, match=str(MAX_HNSW_DIM)):
        resolve_embedding_dim(model_id)

def test_exactly_at_limit_ok(monkeypatch):
    _patch_embed(monkeypatch, dim=MAX_HNSW_DIM)
    assert resolve_embedding_dim("fake/model-2000") == MAX_HNSW_DIM
