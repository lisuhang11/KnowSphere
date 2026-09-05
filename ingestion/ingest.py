"""文档摄取（兼容入口）。

实现已迁至 services.ingestion_service.IngestionService；
本模块保留 CLI 与 `ingest_file` / `reparse_document` 函数签名供 Celery、evals 调用。
"""

from __future__ import annotations

import sys

from services.ingestion_service import (
    IngestionService,
    create_splitter,
    resolve_chunk_config,
)

_default = IngestionService()

def ingest_file(*args, **kwargs):
    return _default.ingest_file(*args, **kwargs)

def reparse_document(*args, **kwargs):
    return _default.reparse_document(*args, **kwargs)

__all__ = [
    "IngestionService",
    "create_splitter",
    "ingest_file",
    "reparse_document",
    "resolve_chunk_config",
]

if __name__ == "__main__":
    if len(sys.argv) not in (2, 4):
        print("用法: python -m ingestion.ingest <文档路径> [--kb-id <知识库ID>]")
        raise SystemExit(1)
    kb_id = None
    if len(sys.argv) == 4 and sys.argv[2] == "--kb-id":
        kb_id = int(sys.argv[3])
    print(ingest_file(sys.argv[1], kb_id=kb_id))
    from utils.observability import flush_langfuse

    flush_langfuse()
