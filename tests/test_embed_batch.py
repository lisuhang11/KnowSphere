"""embedding 构建与分批向量化回归。"""

from __future__ import annotations

from ingestion.embed_batch import embed_documents_batched
from models.siliconflow import build_embeddings

class _FakeEmbeddings:
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i)] for i in range(len(texts))]

def test_build_embeddings_skips_tiktoken_ctx_check():
    emb = build_embeddings(model="BAAI/bge-m3")
    assert emb.check_embedding_ctx_length is False

def test_embed_documents_batched_splits_and_progress():
    fake = _FakeEmbeddings()
    progress: list[tuple[int, int]] = []
    texts = [f"c{i}" for i in range(5)]
    vecs = embed_documents_batched(
        fake,
        texts,
        batch_size=2,
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert len(vecs) == 5
    assert fake.calls == [["c0", "c1"], ["c2", "c3"], ["c4"]]
    assert progress == [(2, 5), (4, 5), (5, 5)]
