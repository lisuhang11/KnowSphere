"""会话临时附件 API（/sessions/{id}/attachments）。"""

from __future__ import annotations

import asyncio
import logging
import uuid

from urllib.parse import unquote

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from api.sessions import _db_get_session, _to_uuid
from api.tasks import parse_temporary_attachment_task
from config.settings import settings
from utils.chat_images import NO_VLM_IMAGE_UPLOAD_DETAIL
from utils.model_store import ModelStore
from utils.object_store import ObjectStoreError, inline_content_disposition, require_object_store
from utils.temporary_attachments import (
    TemporaryAttachmentStore,
    is_image_upload,
    validate_attachment_file,
)

logger = logging.getLogger(__name__)

attachments_router = APIRouter(prefix="/sessions", tags=["attachments"])


def _public_attachment(row: dict) -> dict:
    public = {k: v for k, v in row.items() if k not in ("storage_key", "content", "chunks")}
    public["image_refs"] = [
        {k: v for k, v in ref.items() if k != "storage_key"}
        for ref in (row.get("image_refs") or [])
        if isinstance(ref, dict)
    ]
    return public

def _ensure_session_exists(session_id: uuid.UUID) -> None:
    if _db_get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话不存在")

@attachments_router.post("/{session_id}/attachments", status_code=202)
async def upload_temporary_attachment(
    session_id: str,
    file: UploadFile = File(...),
) -> dict:
    """上传临时附件，异步解析。"""
    if not settings.chat_images_enabled:
        raise HTTPException(status_code=400, detail="附件上传未启用")
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)

    file_name = (file.filename or "upload").strip()
    if is_image_upload(file_name, file.content_type or "") and not ModelStore().has_usable_vlm():
        raise HTTPException(status_code=400, detail=NO_VLM_IMAGE_UPLOAD_DETAIL)

    data = await file.read()
    try:
        validate_attachment_file(file_name, len(data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        row = await asyncio.to_thread(
            TemporaryAttachmentStore().create,
            session_id=str(sid),
            file_name=file_name,
            mime_type=file.content_type or "",
            file_size=len(data),
            data=data,
        )
    except ObjectStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        parse_temporary_attachment_task.delay(row["id"], str(sid))
    except Exception as exc:
        logger.warning("投递附件解析任务失败 %s: %s", row["id"], exc)
        TemporaryAttachmentStore().mark_failed(row["id"], f"任务投递失败: {exc}")

    return {"success": True, "data": _public_attachment(row)}

@attachments_router.get("/{session_id}/attachments")
async def list_temporary_attachments(session_id: str) -> dict:
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)
    rows = await asyncio.to_thread(TemporaryAttachmentStore().list_by_session, str(sid))
    return {"success": True, "data": rows}

@attachments_router.get("/{session_id}/attachments/{attachment_id}")
async def get_temporary_attachment(session_id: str, attachment_id: str) -> dict:
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)
    row = await asyncio.to_thread(TemporaryAttachmentStore().get, attachment_id, str(sid))
    if row is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    return {"success": True, "data": _public_attachment(row)}

@attachments_router.get("/{session_id}/attachments/{attachment_id}/preview")
async def preview_temporary_attachment(session_id: str, attachment_id: str) -> Response:
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)
    meta = await asyncio.to_thread(TemporaryAttachmentStore().get_storage_key, attachment_id, str(sid))
    if meta is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    storage_key, file_name = meta
    try:
        store = require_object_store()
        data, content_type = await asyncio.to_thread(store.get_bytes, storage_key)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": inline_content_disposition(file_name),
        },
    )


@attachments_router.get("/{session_id}/attachments/{attachment_id}/images/{filename}")
async def preview_extracted_attachment_image(
    session_id: str,
    attachment_id: str,
    filename: str,
) -> Response:
    """附件解析抽出的内嵌图（对齐知识库 /documents/{id}/images/{name}）。"""
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)
    safe = unquote(filename or "")
    meta = await asyncio.to_thread(
        TemporaryAttachmentStore().get_extracted_image,
        attachment_id,
        str(sid),
        safe,
    )
    if meta is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    storage_key, mime_type = meta
    try:
        store = require_object_store()
        data, content_type = await asyncio.to_thread(store.get_bytes, storage_key)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=mime_type or content_type or "image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": inline_content_disposition(safe),
        },
    )

@attachments_router.delete("/{session_id}/attachments/{attachment_id}", status_code=204)
async def delete_temporary_attachment(session_id: str, attachment_id: str) -> None:
    sid = _to_uuid(session_id)
    _ensure_session_exists(sid)
    deleted = await asyncio.to_thread(TemporaryAttachmentStore().delete, attachment_id, str(sid))
    if not deleted:
        raise HTTPException(status_code=404, detail="附件不存在")
