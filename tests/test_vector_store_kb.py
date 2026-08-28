"""回归：多知识库存储层（KB 生命周期 + kb_ids 检索过滤 + 维度列/上限）。

纯函数用例不依赖 DB；生命周期用例在 postgres 可用时执行（否则 skip）。
"""

from __future__ import annotations

import uuid

import pytest

from config.settings import settings
from utils.vector_store import ChunkStore, _embedding_column, _kb_cols_prefixed

# ---------- 纯函数 ----------

def test_embedding_column_default_dim():
    assert _embedding_column(settings.embedding_dim) == "embedding"

def test_embedding_column_custom_dim():
    assert _embedding_column(768) == "embedding_768"

def test_kb_cols_prefixed_has_prefix_on_every_col():
    prefixed = _kb_cols_prefixed("kb")
    assert prefixed.startswith("kb.id,")
    assert all(col.strip().startswith("kb.") for col in prefixed.split(","))

def test_create_knowledge_base_rejects_over_limit_dim():
    """超 HNSW 上限的维度应在触碰 DB 前就报错。"""
    with pytest.raises(ValueError, match="2000"):
        ChunkStore().create_knowledge_base(
            name="over-limit", description="", owner="x", embedding_dim=2500
        )

# ---------- DB 集成 ----------

def test_kb_lifecycle_and_kb_ids_filter(pg_available):
    if not pg_available:
        pytest.skip("postgres 未运行")
    store = ChunkStore()
    store.init_schema()
    owner = f"test_{uuid.uuid4().hex[:10]}"
    kb = store.create_knowledge_base(name="it-kb", description="回归", owner=owner)
    kb_id = kb["id"]
    try:
        dim = settings.embedding_dim
        vec = [0.1] * dim
        n = store.insert_batch(
            document_id="d1",
            file_name="f1",
            chunks=["苹果香蕉很好吃", "今天的天气很好"],
            embeddings=[vec, vec],
            owner=owner,
            kb_id=kb_id,
        )
        assert n == 2

        hits = store.hybrid_search("苹果", vec, top_k=5, owner=owner, kb_ids=[kb_id])
        assert len(hits) >= 1
        assert any("苹果" in h["content"] for h in hits)

        # 指定其他 KB → 本题内容不可见
        hits_other = store.hybrid_search(
            "苹果", vec, top_k=5, owner=owner, kb_ids=[kb_id + 1000000]
        )
        assert hits_other == []

        # 不指定 kb_ids → owner 全量可见
        hits_all = store.hybrid_search("苹果", vec, top_k=5, owner=owner)
        assert len(hits_all) >= 1
    finally:
        store.delete_knowledge_base(kb_id, owner=owner)
        assert store.get_knowledge_base(kb_id, owner=owner) is None
        # chunks 一并删除
        hits_after = store.hybrid_search(
            "苹果", [0.1] * dim, top_k=5, owner=owner, kb_ids=[kb_id]
        )
        assert hits_after == []
