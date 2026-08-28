"""文档上传/重解析的配置解析（供 documents 路由复用）。"""

from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import BaseModel

from chunkers import VALID_STRATEGIES
from config.settings import settings

CHUNK_SIZE_MIN, CHUNK_SIZE_MAX = 64, 4096
CHUNK_OVERLAP_MAX = 1024
PARENT_CHUNK_SIZE_MAX = 8192
CHILD_CHUNK_SIZE_MAX = 2048
PREVIEW_MAX_CHARS = 64 * 1024

class ReparseRequest(BaseModel):
    process_config: dict | None = None

def parse_process_config(raw: str | None) -> dict | None:
    """解析上传/重新解析携带的文档级 process_config（JSON 字符串 → 清洗后的 dict）。"""
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"process_config 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="process_config 必须是 JSON 对象")
    chunking = data.get("chunking_config") or {}
    if not isinstance(chunking, dict):
        raise HTTPException(status_code=400, detail="process_config.chunking_config 必须是对象")
    cleaned: dict = {}
    if "strategy" in chunking:
        if chunking["strategy"] not in VALID_STRATEGIES:
            raise HTTPException(
                status_code=400,
                detail=f"无效 strategy: {chunking['strategy']}（可选: {', '.join(VALID_STRATEGIES)}）",
            )
        cleaned["strategy"] = chunking["strategy"]
    if "chunk_size" in chunking and chunking["chunk_size"] is not None:
        size = chunking["chunk_size"]
        if not isinstance(size, int) or not (CHUNK_SIZE_MIN <= size <= CHUNK_SIZE_MAX):
            raise HTTPException(status_code=400, detail=f"chunk_size 必须是 {CHUNK_SIZE_MIN}-{CHUNK_SIZE_MAX} 的整数")
        cleaned["chunk_size"] = size
    if "chunk_overlap" in chunking and chunking["chunk_overlap"] is not None:
        overlap = chunking["chunk_overlap"]
        if not isinstance(overlap, int) or not (0 <= overlap <= CHUNK_OVERLAP_MAX):
            raise HTTPException(status_code=400, detail=f"chunk_overlap 必须是 0-{CHUNK_OVERLAP_MAX} 的整数")
        cleaned["chunk_overlap"] = overlap
    if "enable_parent_child" in chunking and chunking["enable_parent_child"] is not None:
        cleaned["enable_parent_child"] = bool(chunking["enable_parent_child"])
    if "parent_chunk_size" in chunking and chunking["parent_chunk_size"] is not None:
        psize = chunking["parent_chunk_size"]
        if not isinstance(psize, int) or not (CHUNK_SIZE_MIN <= psize <= PARENT_CHUNK_SIZE_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"parent_chunk_size 必须是 {CHUNK_SIZE_MIN}-{PARENT_CHUNK_SIZE_MAX} 的整数",
            )
        cleaned["parent_chunk_size"] = psize
    if "child_chunk_size" in chunking and chunking["child_chunk_size"] is not None:
        csize = chunking["child_chunk_size"]
        if not isinstance(csize, int) or not (CHUNK_SIZE_MIN <= csize <= CHILD_CHUNK_SIZE_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"child_chunk_size 必须是 {CHUNK_SIZE_MIN}-{CHILD_CHUNK_SIZE_MAX} 的整数",
            )
        cleaned["child_chunk_size"] = csize
    if not cleaned:
        return None
    return {"chunking_config": cleaned}
