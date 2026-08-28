"""知识库管理 API：多知识库 CRUD + 文档移动。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import KnowledgeBaseService, get_knowledge_base_service
from api.service_http import map_service_error
from config.settings import settings
from models.dimensions import resolve_embedding_dim
from utils.model_credentials import ensure_embedding_model_ready, ensure_knowledgeqa_model_ready
from utils.model_store import ModelStore

router = APIRouter(tags=["knowledge-bases"])

class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    chunk_size: int | None = Field(default=None, ge=64, le=4096)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1024)
    embedding_model_id: str | None = Field(default=None, max_length=200)
    summary_model_id: str | None = Field(default=None, max_length=200)
    chunk_strategy: str = Field(
        default="auto",
        pattern="^(auto|heading|heuristic|recursive)$",
        description="切块策略：auto(自适应)/heading/heuristic/recursive",
    )
    enable_parent_child: bool = Field(default=False, description="启用父子分块（子块检索、父块上下文）")
    parent_chunk_size: int | None = Field(default=None, ge=64, le=8192)
    child_chunk_size: int | None = Field(default=None, ge=64, le=2048)

class KBUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    chunk_size: int | None = Field(default=None, ge=64, le=4096)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1024)
    chunk_strategy: str | None = Field(
        default=None,
        pattern="^(auto|heading|heuristic|recursive)$",
        description="切块策略：auto(自适应)/heading/heuristic/recursive",
    )
    summary_model_id: str | None = Field(default=None, max_length=200)
    enable_parent_child: bool | None = None
    parent_chunk_size: int | None = Field(default=None, ge=64, le=8192)
    child_chunk_size: int | None = Field(default=None, ge=64, le=2048)

class DocMoveRequest(BaseModel):
    kb_id: int

def _validate_embedding_ref(model_id: str) -> str:
    model_id = model_id.strip()
    store = ModelStore()
    if model_id.startswith("model-"):
        if not store.is_embedding_model_id_valid(model_id):
            raise HTTPException(status_code=400, detail="embedding 模型不存在、已禁用或类型不匹配")
        try:
            ensure_embedding_model_ready(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return model_id
    raise HTTPException(
        status_code=400,
        detail="embedding_model_id 须为模型管理中的模型 ID（model-...），请先在「模型管理」创建向量化模型",
    )

def _validate_summary_ref(model_id: str | None) -> str | None:
    if not model_id or not model_id.strip():
        return None
    model_id = model_id.strip()
    store = ModelStore()
    if not model_id.startswith("model-"):
        raise HTTPException(
            status_code=400,
            detail="summary_model_id 须为模型管理中的问答模型 ID（model-...）",
        )
    if not store.is_knowledgeqa_model_id_valid(model_id):
        raise HTTPException(status_code=400, detail="摘要/问答模型不存在、已禁用或类型不匹配")
    try:
        ensure_knowledgeqa_model_ready(model_id, label="摘要/问答")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return model_id

@router.post("/knowledge-bases")
def create_knowledge_base(
    body: KBCreateRequest,
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> dict:
    embedding_id = _validate_embedding_ref(body.embedding_model_id or settings.embedding_model)
    summary_id = _validate_summary_ref(body.summary_model_id)
    try:
        dim = resolve_embedding_dim(embedding_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return kb_svc.create(
            name=body.name.strip(),
            description=body.description.strip(),
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            embedding_model_id=embedding_id,
            embedding_dim=dim,
            chunk_strategy=body.chunk_strategy,
            summary_model_id=summary_id,
            enable_parent_child=body.enable_parent_child,
            parent_chunk_size=body.parent_chunk_size,
            child_chunk_size=body.child_chunk_size,
        )
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="创建知识库失败") from exc

@router.get("/knowledge-bases")
def list_knowledge_bases(
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> list[dict]:
    try:
        return kb_svc.list_all()
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="查询知识库列表失败") from exc

@router.get("/knowledge-bases/{kb_id}")
def get_knowledge_base(
    kb_id: int,
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> dict:
    try:
        return kb_svc.get(kb_id)
    except Exception as exc:
        raise map_service_error(exc) from exc

@router.patch("/knowledge-bases/{kb_id}")
def update_knowledge_base(
    kb_id: int,
    body: KBUpdateRequest,
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> dict:
    summary_id: str | None | object = None
    if body.summary_model_id is not None:
        if body.summary_model_id == "":
            summary_id = ""
        else:
            summary_id = _validate_summary_ref(body.summary_model_id)
    try:
        return kb_svc.update(
            kb_id,
            name=body.name.strip() if body.name is not None else None,
            description=body.description.strip() if body.description is not None else None,
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            chunk_strategy=body.chunk_strategy,
            summary_model_id=summary_id if summary_id is not None else None,
            enable_parent_child=body.enable_parent_child,
            parent_chunk_size=body.parent_chunk_size,
            child_chunk_size=body.child_chunk_size,
        )
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="更新失败") from exc

@router.delete("/knowledge-bases/{kb_id}")
def delete_knowledge_base(
    kb_id: int,
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> dict:
    try:
        return kb_svc.delete(kb_id)
    except Exception as exc:
        raise map_service_error(exc) from exc

@router.post("/documents/{document_id}/move")
def move_document(
    document_id: str,
    body: DocMoveRequest,
    kb_svc: KnowledgeBaseService = Depends(get_knowledge_base_service),
) -> dict:
    try:
        return kb_svc.move_document(document_id, body.kb_id)
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="移动文档失败") from exc
