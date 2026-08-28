"""文档域 API 路由（从 api/main.py 拆出）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from langsmith import traceable

from api import celery_app
from api.deps import DocumentService, get_document_service
from api.document_config import ReparseRequest, parse_process_config
from api.service_http import map_service_error
from api.tasks import process_document_task, reprocess_document_task
from ingestion.parser import ParserError
from ingestion.parser.image_store import get_image_store
from utils.object_store import inline_content_disposition, read_document_bytes, read_document_text

documents_router = APIRouter(tags=["documents"])

PREVIEW_TEXT_LIMIT = 200_000

@documents_router.post("/upload")
@traceable(name="upload_endpoint", run_type="chain")
async def upload(
    file: UploadFile = File(...),
    kb_id: int = Form(..., description="目标知识库 ID"),
    process_config: str | None = Form(
        default=None,
        description='文档级处理配置（JSON 字符串，可选）',
    ),
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    parsed = parse_process_config(process_config)
    file_bytes = await file.read()
    try:
        payload = docs.begin_upload(
            kb_id=kb_id,
            file_name=file.filename or "upload",
            file_bytes=file_bytes,
            owner=owner,
            process_config=parsed,
        )
    except Exception as exc:
        raise map_service_error(exc) from exc

    document_id = payload["document_id"]
    try:
        task = process_document_task.delay(
            document_id=document_id,
            storage_key=payload["storage_key"],
            file_name=payload["file_name"],
            kb_id=kb_id,
            owner=owner,
            process_config=parsed,
        )
    except Exception as exc:
        docs.mark_enqueue_failed(document_id, owner, f"任务入队失败: {exc}")
        task_id = None
    else:
        task_id = task.id

    return JSONResponse(
        status_code=202,
        content={
            "document_id": document_id,
            "file_name": payload["file_name"],
            "kb_id": kb_id,
            "status": payload["status"],
            "task_id": task_id,
        },
    )

@documents_router.get("/documents")
def list_documents(
    kb_id: int = Query(..., description="知识库 ID"),
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> list[dict]:
    try:
        return docs.list_documents(kb_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="查询文档列表失败") from exc

@documents_router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        result = docs.delete(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="删除失败") from exc

    docs.cleanup_object_storage(result.pop("storage_keys", []))
    task_id = result.pop("task_id", None)
    if task_id:
        try:
            celery_app.celery.control.revoke(task_id)
        except Exception:
            pass
    return result

@documents_router.get("/documents/{document_id}/status")
def document_status(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        return docs.get_status(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

@documents_router.post("/documents/{document_id}/cancel")
def cancel_document(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        return docs.cancel(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

@documents_router.get("/documents/{document_id}/meta")
def document_meta(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        return docs.get_meta(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

@documents_router.get("/documents/{document_id}/images/{image_name}")
def document_image(
    document_id: str,
    image_name: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> Response:
    try:
        meta = docs.get_meta(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

    ref = next(
        (r for r in (meta.get("image_refs") or []) if r.get("filename") == image_name),
        None,
    )
    if ref is None or not ref.get("storage_key"):
        raise HTTPException(status_code=404, detail=f"图片不存在: {image_name}")
    store = get_image_store()
    if store is None:
        raise HTTPException(status_code=503, detail="MinIO 未配置，无法读取图片")
    try:
        data, content_type = store.get_image(ref["storage_key"])
    except ParserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type or "image/jpeg")

@documents_router.post("/documents/{document_id}/reparse")
def reparse_document(
    document_id: str,
    body: ReparseRequest | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        ctx = docs.prepare_reparse(document_id)
    except Exception as exc:
        raise map_service_error(exc) from exc

    pc = body.process_config if body and body.process_config is not None else ctx.get("process_config")
    try:
        task = reprocess_document_task.delay(
            document_id=document_id,
            owner=None,
            process_config=pc,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"任务入队失败: {exc}") from exc

    return JSONResponse(
        status_code=202,
        content={
            "document_id": document_id,
            "status": docs.store.STATUS_PROCESSING,
            "task_id": task.id,
        },
    )

@documents_router.get("/documents/{document_id}/chunks")
def list_document_chunks(
    document_id: str,
    owner: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    include_parent_text: bool = Query(default=False, description="是否包含 parent_text 父块"),
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        return docs.list_chunks(
            document_id,
            owner=owner,
            page=page,
            page_size=page_size,
            include_parent_text=include_parent_text,
        )
    except Exception as exc:
        raise map_service_error(exc, default_status=500, default_prefix="查询分块列表失败") from exc

@documents_router.get("/chunks/{chunk_id}")
def get_chunk_by_id(
    chunk_id: int,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        return docs.get_chunk(chunk_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

@documents_router.get("/documents/{document_id}/file")
def preview_document_file(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> Response:
    """内联预览原始文件（PDF/图片等），对齐 WeKnora GET /knowledge/:id/preview。"""
    try:
        meta = docs.get_meta(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

    real_name = Path(meta["file_name"]).name
    try:
        data, content_type = read_document_bytes(meta.get("stored_name"), real_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": inline_content_disposition(real_name),
            "Cache-Control": "private, max-age=3600",
        },
    )


@documents_router.get("/documents/{document_id}/preview")
def preview_document(
    document_id: str,
    owner: str | None = None,
    docs: DocumentService = Depends(get_document_service),
) -> dict:
    try:
        meta = docs.get_meta(document_id, owner=owner)
    except Exception as exc:
        raise map_service_error(exc) from exc

    real_name = Path(meta["file_name"]).name
    ext = Path(real_name).suffix.lower()
    if ext not in (".md", ".txt"):
        raise HTTPException(status_code=400, detail=f"{ext} 格式暂不支持原文预览，仅支持 md/txt")

    try:
        content = read_document_text(meta.get("stored_name"), real_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "document_id": document_id,
        "file_name": real_name,
        "content": content[:PREVIEW_TEXT_LIMIT],
        "truncated": len(content) > PREVIEW_TEXT_LIMIT,
    }
