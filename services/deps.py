"""Service / Store 工厂（便于测试注入）。"""

from __future__ import annotations

from functools import lru_cache

from services.document_service import DocumentService
from services.document_task_service import DocumentTaskService
from services.ingestion_service import IngestionService
from services.knowledge_base_service import KnowledgeBaseService
from services.retrieval_service import RetrievalService
from stores.facade import ChunkStore


@lru_cache(maxsize=1)
def get_chunk_store() -> ChunkStore:
    return ChunkStore()


@lru_cache(maxsize=1)
def get_document_service() -> DocumentService:
    return DocumentService(get_chunk_store())


@lru_cache(maxsize=1)
def get_knowledge_base_service() -> KnowledgeBaseService:
    return KnowledgeBaseService(get_chunk_store())


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    return RetrievalService(get_chunk_store())


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    return IngestionService(get_chunk_store())


@lru_cache(maxsize=1)
def get_document_task_service() -> DocumentTaskService:
    store = get_chunk_store()
    return DocumentTaskService(store, IngestionService(store))
