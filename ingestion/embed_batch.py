"""分批向量化：按批写入 embedding 的分批语义。

长文档会产生大量 chunk；分批调用 embedding API 可避免单次请求过大，
并在每批完成后触发进度回调（用于刷新 documents.updated_at 心跳）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from langchain_core.embeddings import Embeddings

from config.settings import settings

def embed_documents_batched(
    embeddings: Embeddings,
    texts: Sequence[str],
    batch_size: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    """按 batch_size 分批 embed_documents，返回与 texts 等长的向量列表。"""
    if not texts:
        return []
    size = batch_size or settings.embedding_batch_size
    vectors: list[list[float]] = []
    total = len(texts)
    for start in range(0, total, size):
        batch = list(texts[start:start + size])
        vectors.extend(embeddings.embed_documents(batch))
        if on_progress:
            on_progress(min(start + len(batch), total), total)
    return vectors
