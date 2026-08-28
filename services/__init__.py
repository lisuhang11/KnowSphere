"""业务编排层（API / Agent / Celery 与 stores 之间的薄中间层）。

避免在包导入时拉取 RetrievalService，防止与 tools → deps 形成环。
"""

from services.ingestion_service import IngestionService, create_splitter, resolve_chunk_config

__all__ = [
    "IngestionService",
    "create_splitter",
    "resolve_chunk_config",
]
