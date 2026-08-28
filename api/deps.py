"""FastAPI Depends 注入。"""

from __future__ import annotations

from services.deps import (
    get_document_service,
    get_knowledge_base_service,
    get_retrieval_service,
)
from services.document_service import DocumentService
from services.knowledge_base_service import KnowledgeBaseService
from services.retrieval_service import RetrievalService

__all__ = [
    "DocumentService",
    "KnowledgeBaseService",
    "RetrievalService",
    "get_document_service",
    "get_knowledge_base_service",
    "get_retrieval_service",
]
