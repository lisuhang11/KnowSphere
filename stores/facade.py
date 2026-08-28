"""持久化门面：组合各 Repository，对外保持 ChunkStore() API 不变。"""

from __future__ import annotations

from stores.chunk_repository import ChunkRepository
from stores.document_repository import DocumentRepository
from stores.knowledge_base_repository import KnowledgeBaseRepository
from stores.schema import init_schema

class ChunkStore():
    """文档分块向量存储（Postgres + pgvector + pg_trgm）。

    内部分拆为 Document / Chunk / KnowledgeBase 三个 Repository；
    本类仅做委托，保证 `from utils.vector_store import ChunkStore` 零改动。
    """

    STATUS_PENDING = DocumentRepository.STATUS_PENDING
    STATUS_PROCESSING = DocumentRepository.STATUS_PROCESSING
    STATUS_COMPLETED = DocumentRepository.STATUS_COMPLETED
    STATUS_FAILED = DocumentRepository.STATUS_FAILED
    STATUS_CANCELLED = DocumentRepository.STATUS_CANCELLED

    def __init__(self, dsn: str | None = None) -> None:
        from config.settings import settings

        self.dsn = dsn or settings.postgres_dsn
        self._chunks = ChunkRepository(self.dsn)
        self._documents = DocumentRepository(self.dsn)
        self._knowledge_bases = KnowledgeBaseRepository(self.dsn, self._chunks)

    def init_schema(self) -> None:
        init_schema(self.dsn)

    # --- chunk ---
    def ensure_embedding_column(self, dim: int) -> None:
        return self._chunks.ensure_embedding_column(dim)

    def insert_batch(self, *args, **kwargs):
        return self._chunks.insert_batch(*args, **kwargs)

    def insert_parent_child_batch(self, *args, **kwargs):
        return self._chunks.insert_parent_child_batch(*args, **kwargs)

    def replace_document_parent_child_batch(self, *args, **kwargs):
        return self._chunks.replace_document_parent_child_batch(*args, **kwargs)

    def get_chunks_by_ids(self, *args, **kwargs):
        return self._chunks.get_chunks_by_ids(*args, **kwargs)

    def replace_document_chunks(self, *args, **kwargs):
        return self._chunks.replace_document_chunks(*args, **kwargs)

    def hybrid_search(self, *args, **kwargs):
        return self._chunks.hybrid_search(*args, **kwargs)

    def list_chunks(self, *args, **kwargs):
        return self._chunks.list_chunks(*args, **kwargs)

    def get_document_chunks_content(self, *args, **kwargs):
        return self._chunks.get_document_chunks_content(*args, **kwargs)

    # --- document ---
    def upsert_document(self, *args, **kwargs):
        return self._documents.upsert_document(*args, **kwargs)

    def get_document_config(self, *args, **kwargs):
        return self._documents.get_document_config(*args, **kwargs)

    def get_document_meta(self, *args, **kwargs):
        return self._documents.get_document_meta(*args, **kwargs)

    def list_documents(self, *args, **kwargs):
        return self._documents.list_documents(*args, **kwargs)

    def delete_document(self, *args, **kwargs):
        return self._documents.delete_document(*args, **kwargs)

    def get_document_status_row(self, *args, **kwargs):
        return self._documents.get_document_status_row(*args, **kwargs)

    def transition_document_status(self, *args, **kwargs):
        return self._documents.transition_document_status(*args, **kwargs)

    def insert_document_pending(self, *args, **kwargs):
        return self._documents.insert_document_pending(*args, **kwargs)

    def mark_document_processing(self, *args, **kwargs):
        return self._documents.mark_document_processing(*args, **kwargs)

    def update_stage(self, *args, **kwargs):
        return self._documents.update_stage(*args, **kwargs)

    def mark_document_completed(self, *args, **kwargs):
        return self._documents.mark_document_completed(*args, **kwargs)

    def mark_document_failed(self, *args, **kwargs):
        return self._documents.mark_document_failed(*args, **kwargs)

    def mark_document_cancelled(self, *args, **kwargs):
        return self._documents.mark_document_cancelled(*args, **kwargs)

    def fail_stale_processing(self, *args, **kwargs):
        return self._documents.fail_stale_processing(*args, **kwargs)

    def get_document_kb_id(self, *args, **kwargs):
        return self._documents.get_document_kb_id(*args, **kwargs)

    # --- knowledge base ---
    def create_knowledge_base(self, *args, **kwargs):
        return self._knowledge_bases.create_knowledge_base(*args, **kwargs)

    def list_knowledge_bases(self, *args, **kwargs):
        return self._knowledge_bases.list_knowledge_bases(*args, **kwargs)

    def get_knowledge_base(self, *args, **kwargs):
        return self._knowledge_bases.get_knowledge_base(*args, **kwargs)

    def get_knowledge_base_configs(self, *args, **kwargs):
        return self._knowledge_bases.get_knowledge_base_configs(*args, **kwargs)

    def update_knowledge_base(self, *args, **kwargs):
        return self._knowledge_bases.update_knowledge_base(*args, **kwargs)

    def delete_knowledge_base(self, *args, **kwargs):
        return self._knowledge_bases.delete_knowledge_base(*args, **kwargs)

    def move_document(self, *args, **kwargs):
        return self._knowledge_bases.move_document(*args, **kwargs)
