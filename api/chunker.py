"""切块预览 API（从 api/main.py 拆出）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.document_config import PREVIEW_MAX_CHARS
from chunkers import split_parent_child_with_diagnostics, split_with_diagnostics
from config.settings import settings
from utils.tokens import estimate_tokens
from utils.vector_store import ChunkStore

chunker_router = APIRouter(tags=["chunker"])

class PreviewChunkingRequest(BaseModel):
    text: str
    kb_id: int | None = None
    strategy: str = "auto"
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    enable_parent_child: bool | None = None
    parent_chunk_size: int | None = None
    child_chunk_size: int | None = None

@chunker_router.post("/preview-chunking")
def preview_chunking(req: PreviewChunkingRequest) -> dict:
    text = req.text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is empty — paste a sample to preview chunking")
    if len(text) > PREVIEW_MAX_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"text exceeds preview limit: {PREVIEW_MAX_CHARS} chars",
        )

    chunk_size, chunk_overlap, strategy = (
        settings.chunk_size,
        settings.chunk_overlap,
        req.strategy,
    )
    enable_parent_child = settings.enable_parent_child
    parent_chunk_size = settings.parent_chunk_size
    child_chunk_size = settings.child_chunk_size
    if req.kb_id is not None:
        kb = ChunkStore().get_knowledge_base(req.kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail=f"知识库不存在: {req.kb_id}")
        chunk_size, chunk_overlap = kb["chunk_size"], kb["chunk_overlap"]
        enable_parent_child = kb.get("enable_parent_child", False)
        parent_chunk_size = kb.get("parent_chunk_size", settings.parent_chunk_size)
        child_chunk_size = kb.get("child_chunk_size", settings.child_chunk_size)
        if req.strategy == "auto":
            strategy = kb["chunk_strategy"]
    if req.chunk_size is not None:
        chunk_size = req.chunk_size
    if req.chunk_overlap is not None:
        chunk_overlap = req.chunk_overlap
    if req.enable_parent_child is not None:
        enable_parent_child = req.enable_parent_child
    if req.parent_chunk_size is not None:
        parent_chunk_size = req.parent_chunk_size
    if req.child_chunk_size is not None:
        child_chunk_size = req.child_chunk_size

    try:
        if enable_parent_child:
            pc = split_parent_child_with_diagnostics(
                text,
                strategy=strategy,
                parent_size=parent_chunk_size,
                child_size=child_chunk_size,
                chunk_overlap=chunk_overlap,
            )
        else:
            result = split_with_diagnostics(
                text, strategy=strategy, chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if enable_parent_child:
        return {
            "enable_parent_child": True,
            "parent_chunk_size": pc.parent_chunk_size,
            "child_chunk_size": pc.child_chunk_size,
            "chunk_overlap": pc.chunk_overlap,
            "strategy": pc.strategy,
            "selected_tier": pc.selected_tier,
            "tier_chain": pc.tier_chain,
            "rejected": pc.rejected,
            "profile": pc.profile,
            "stats": pc.stats,
            "parent_count": len(pc.parents),
            "chunk_count": len(pc.children),
            "chunks": [
                {
                    "seq": i,
                    "content": c.content,
                    "context_header": c.context_header,
                    "parent_index": c.parent_index,
                    "char_count": len(c.content),
                    "token_count": estimate_tokens(c.content),
                }
                for i, c in enumerate(pc.children)
            ],
        }

    return {
        "enable_parent_child": False,
        "chunk_size": result.chunk_size,
        "chunk_overlap": result.chunk_overlap,
        "strategy": result.strategy,
        "selected_tier": result.selected_tier,
        "tier_chain": result.tier_chain,
        "rejected": result.rejected,
        "profile": result.profile,
        "stats": result.stats,
        "chunk_count": len(result.chunks),
        "chunks": [
            {
                "seq": i,
                "content": c.content,
                "context_header": c.context_header,
                "char_count": len(c.content),
                "token_count": estimate_tokens(c.content),
            }
            for i, c in enumerate(result.chunks)
        ],
    }
