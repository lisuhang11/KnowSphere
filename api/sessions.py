"""会话 API。

对外前缀 `/sessions`。
LangGraph checkpoint 使用与 session `id` 相同的 UUID 作为 `thread_id` 键。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from api.chat import get_agent, get_checkpointer
from config.settings import settings
from utils.chat_images import (
    ChatImageError,
    build_human_message_saved_images_only,
    load_chat_image_bytes,
    save_chat_images,
)
from utils.attachment_resolve import (
    build_human_message_with_attachments,
    normalize_attachment_ids,
    resolve_attachments,
)
from utils.citation import Citation, CitationStreamExpander, merge_citation_maps
from utils.model_credentials import ensure_knowledgeqa_model_ready
from utils.model_store import ModelStore
from utils.vector_store import ChunkStore

logger = logging.getLogger(__name__)

sessions_router = APIRouter(prefix="/sessions", tags=["sessions"])

# ---------------------------------------------------------------------------
# 会话元数据（ks_threads 表，历史表名保留）
# ---------------------------------------------------------------------------

def ensure_session_table() -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ks_threads (
                thread_id  UUID PRIMARY KEY,
                title      TEXT,
                kb_ids     BIGINT[] NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS kb_ids BIGINT[] NOT NULL DEFAULT '{}'"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false"
        )
        conn.execute(
            "ALTER TABLE ks_threads ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ"
        )
        conn.commit()

_SESSION_SELECT = "thread_id, title, kb_ids, created_at, updated_at, is_pinned, pinned_at"

def _sanitize_kb_ids(raw: Any) -> list[int]:
    if not isinstance(raw, (list, tuple)):
        return []
    ids: list[int] = []
    for v in raw:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    return ids

def _row_to_session(row) -> dict[str, Any]:
    thread_id, title, kb_ids, created_at, updated_at, is_pinned, pinned_at = row
    sid = str(thread_id)
    kb_list = list(kb_ids) if kb_ids else []
    metadata: dict[str, Any] = {}
    if title:
        metadata["title"] = title
    if kb_list:
        metadata["kb_ids"] = kb_list
    return {
        "id": sid,
        "thread_id": sid,  # LangGraph checkpoint 键（兼容旧前端）
        "title": title or "",
        "kb_ids": kb_list,
        "metadata": metadata,
        "created_at": created_at.isoformat() if created_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "is_pinned": bool(is_pinned),
        "pinned_at": pinned_at.isoformat() if pinned_at else None,
    }

def _to_uuid(session_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"非法 session_id: {session_id}") from None

def _db_create_session(session_id: uuid.UUID, title: str | None, kb_ids: list[int]) -> dict[str, Any]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            f"INSERT INTO ks_threads (thread_id, title, kb_ids) VALUES (%s, %s, %s) "
            f"RETURNING {_SESSION_SELECT}",
            (session_id, title, kb_ids),
        ).fetchone()
        conn.commit()
    return _row_to_session(row)

def _db_list_sessions(limit: int) -> list[dict[str, Any]]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        rows = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads "
            "ORDER BY is_pinned DESC, pinned_at DESC NULLS LAST, updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]

def _db_get_session(session_id: uuid.UUID) -> dict[str, Any] | None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
    return _row_to_session(row) if row else None

def _db_update_session(
    session_id: uuid.UUID,
    title: str | None = None,
    kb_ids: list[int] | None = None,
) -> dict[str, Any]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()",
            (session_id,),
        )
        if title is not None:
            conn.execute(
                "UPDATE ks_threads SET title = %s WHERE thread_id = %s",
                (title, session_id),
            )
        if kb_ids is not None:
            conn.execute(
                "UPDATE ks_threads SET kb_ids = %s WHERE thread_id = %s",
                (kb_ids, session_id),
            )
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _row_to_session(row)

def _db_delete_session(session_id: uuid.UUID) -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute("DELETE FROM ks_threads WHERE thread_id = %s", (session_id,))
        conn.commit()

def _db_set_pinned(session_id: uuid.UUID, pinned: bool) -> dict[str, Any]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO NOTHING",
            (session_id,),
        )
        if pinned:
            conn.execute(
                "UPDATE ks_threads SET is_pinned = TRUE, pinned_at = now() WHERE thread_id = %s",
                (session_id,),
            )
        else:
            conn.execute(
                "UPDATE ks_threads SET is_pinned = FALSE, pinned_at = NULL WHERE thread_id = %s",
                (session_id,),
            )
        row = conn.execute(
            f"SELECT {_SESSION_SELECT} FROM ks_threads WHERE thread_id = %s",
            (session_id,),
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _row_to_session(row)

def _db_touch_session(session_id: uuid.UUID) -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            "INSERT INTO ks_threads (thread_id) VALUES (%s) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = now()",
            (session_id,),
        )
        conn.commit()

def _db_get_session_kb_ids(session_id: uuid.UUID) -> list[int]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT kb_ids FROM ks_threads WHERE thread_id = %s", (session_id,)
        ).fetchone()
    return list(row[0]) if row and row[0] else []

# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------

class SessionCreateRequest(BaseModel):
    title: str | None = None
    kb_ids: list[int] | None = None
    metadata: dict[str, Any] | None = None  # LangGraph 兼容：{title, kb_ids}

class SessionUpdateRequest(BaseModel):
    title: str | None = None
    kb_ids: list[int] | None = None
    metadata: dict[str, Any] | None = None  # LangGraph PATCH 兼容

class ChatImageAttachment(BaseModel):
    """聊天 inline 图片（前端 base64 data URI）。"""

    data: str = Field(..., description="data:image/png;base64,...")
    url: str | None = Field(default=None, description="客户端忽略，服务端填充")
    caption: str | None = Field(default=None, description="客户端忽略，服务端填充")

class SessionStreamRequest(BaseModel):
    message: str | None = Field(default=None, description="用户消息（推荐）")
    input: dict[str, Any] | None = Field(default=None, description="LangGraph 兼容 input.messages")
    kb_ids: list[int] | None = Field(default=None, description="本轮检索范围（knowledge_base_ids）")
    chat_model_id: str | None = Field(
        default=None,
        description="本轮问答模型 ID（model-...）；缺省则用知识库 summary_model_id 或默认问答模型",
    )
    vlm_model_id: str | None = Field(
        default=None,
        description="本轮视觉理解模型 ID（VLLM）；缺省则用 CHAT_VLM_MODEL_ID 或默认 VLLM",
    )
    images: list[ChatImageAttachment] | None = Field(
        default=None,
        description="本轮附带的图片（base64 data URI，Embed 降级路径）",
    )
    attachment_ids: list[str] | None = Field(
        default=None,
        description="预上传的会话临时附件 ID（Web 推荐路径，最多 5 个）",
    )
    stream_mode: list[str] | None = None
    assistant_id: str | None = None

def _parse_create(body: SessionCreateRequest) -> tuple[str | None, list[int]]:
    meta = body.metadata or {}
    title = body.title or meta.get("title")
    if title is not None and not isinstance(title, str):
        title = str(title)
    kb_raw = body.kb_ids if body.kb_ids is not None else meta.get("kb_ids")
    return title, _sanitize_kb_ids(kb_raw)

def _parse_update(body: SessionUpdateRequest) -> tuple[str | None, list[int] | None]:
    meta = body.metadata or {}
    title = body.title if body.title is not None else meta.get("title")
    if title is not None and not isinstance(title, str):
        title = str(title)
    kb_raw = body.kb_ids if body.kb_ids is not None else meta.get("kb_ids")
    kb_ids = _sanitize_kb_ids(kb_raw) if kb_raw is not None else None
    return title, kb_ids

# ---------------------------------------------------------------------------
# 标准 /sessions 路由
# ---------------------------------------------------------------------------

@sessions_router.post("")
async def create_session(body: SessionCreateRequest) -> dict[str, Any]:
    """CreateSession"""
    title, kb_ids = _parse_create(body)
    return await asyncio.to_thread(_db_create_session, uuid.uuid4(), title, kb_ids)

@sessions_router.get("")
async def list_sessions(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """ListSessions"""
    return await asyncio.to_thread(_db_list_sessions, limit)

@sessions_router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    """GetSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_get_session, sid)
    if row is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return row

@sessions_router.put("/{session_id}")
async def update_session(session_id: str, body: SessionUpdateRequest) -> dict[str, Any]:
    """UpdateSession"""
    sid = _to_uuid(session_id)
    title, kb_ids = _parse_update(body)
    return await asyncio.to_thread(_db_update_session, sid, title, kb_ids)

@sessions_router.post("/{session_id}/pin")
async def pin_session(session_id: str) -> dict[str, Any]:
    """PinSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_set_pinned, sid, True)
    return {"success": True, "is_pinned": row["is_pinned"], "session": row}

@sessions_router.delete("/{session_id}/pin")
async def unpin_session(session_id: str) -> dict[str, Any]:
    """UnpinSession"""
    sid = _to_uuid(session_id)
    row = await asyncio.to_thread(_db_set_pinned, sid, False)
    return {"success": True, "is_pinned": row["is_pinned"], "session": row}

@sessions_router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """DeleteSession"""
    sid = _to_uuid(session_id)
    await asyncio.to_thread(_db_delete_session, sid)
    checkpointer = get_checkpointer()
    if checkpointer is not None:
        try:
            await checkpointer.adelete_thread(str(sid))
        except Exception as e:
            logger.warning("删除 checkpoint 失败（元数据已删）: %s", e)
    return {"deleted": True, "id": str(sid)}

@sessions_router.delete("/{session_id}/messages")
async def clear_session_messages(session_id: str) -> dict[str, Any]:
    """ClearSessionMessages"""
    _to_uuid(session_id)  # 校验格式
    agent = get_agent()
    config = {"configurable": {"thread_id": session_id}}
    await agent.aupdate_state(config, {"messages": []})
    return {"cleared": True, "id": session_id}

@sessions_router.get("/{session_id}/state")
async def get_session_state(session_id: str) -> dict[str, Any]:
    """GetSessionState（LangGraph checkpoint 消息历史）"""
    _to_uuid(session_id)
    agent = get_agent()
    snap = await agent.aget_state({"configurable": {"thread_id": session_id}})
    if not snap or snap.values is None:
        return {"values": {}}
    messages = [m.model_dump(mode="json") for m in snap.values.get("messages", [])]
    return {"values": {"messages": messages}}

# ---------------------------------------------------------------------------
# 流式对话
# ---------------------------------------------------------------------------

_ROLE_MAP = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}
_TYPE_MAP = {"human": HumanMessage, "ai": AIMessage, "system": SystemMessage}

def _parse_input_messages(body: SessionStreamRequest) -> list[Any]:
    if body.message and body.message.strip():
        return [HumanMessage(content=body.message.strip())]
    msgs: list[Any] = []
    for m in (body.input or {}).get("messages") or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if content is None:
            continue
        cls = _ROLE_MAP.get(str(m.get("role", ""))) or _TYPE_MAP.get(str(m.get("type", "")))
        if cls is not None:
            msgs.append(cls(content=content))
    return msgs

async def _build_input_messages(body: SessionStreamRequest, session_id: uuid.UUID) -> list[Any]:
    """解析 inline 图片或纯文本消息（附件在 stream 内异步 resolve）。"""
    query = (body.message or "").strip()
    sid = str(session_id)

    if body.images:
        if not settings.chat_images_enabled:
            raise HTTPException(status_code=400, detail="图片上传未启用")
        query = query or "请分析这张图片"
        data_uris: list[str] = []
        for img in body.images:
            raw = (img.data or "").strip()
            if raw:
                data_uris.append(raw)
        if not data_uris:
            raise HTTPException(status_code=400, detail="images 缺少有效的 data 字段")
        try:
            saved = await asyncio.to_thread(save_chat_images, sid, data_uris)
            return [build_human_message_saved_images_only(query, saved)]
        except ChatImageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    msgs = _parse_input_messages(body)
    if msgs:
        return msgs
    return []

def _attachment_tool_call_frame() -> str:
    return _sse_frame(
        "messages",
        {
            "type": "tool_call",
            "tool_name": "attachment_parsing",
            "content": "正在解析附件…",
        },
    )

def _attachment_tool_result_frame(*, parsed: int, skipped: int) -> str:
    output = f"已解析 {parsed} 个附件"
    if skipped:
        output += f"，{skipped} 个未完成已跳过"
    return _sse_frame(
        "messages",
        {
            "type": "tool_result",
            "tool_name": "attachment_parsing",
            "success": parsed > 0,
            "content": output,
            "parsed_count": parsed,
            "skipped_count": skipped,
        },
    )

def _sse_frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""

def _citations_from_payload(payload: dict[str, Any]) -> dict[int, Citation]:
    citations: dict[int, Citation] = {}
    for c in payload.get("citations") or []:
        idx = int(c["index"])
        citations[idx] = Citation(
            index=idx,
            document_id=str(c.get("document_id", "")),
            file_name=str(c.get("file_name", "")),
            chunk_index=int(c.get("chunk_index") or 0),
            score=float(c.get("score") or 0),
            snippet=str(c.get("snippet") or ""),
        )
    return citations

def _resolve_chat_model_id(body: SessionStreamRequest, kb_ids: list[int]) -> str | None:
    """请求指定 > 知识库 summary_model_id > 默认问答模型。"""
    if body.chat_model_id and body.chat_model_id.strip():
        cid = body.chat_model_id.strip()
        try:
            ensure_knowledgeqa_model_ready(cid, label="问答")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return cid
    if kb_ids:
        configs = ChunkStore().get_knowledge_base_configs(kb_ids)
        for kb_id in kb_ids:
            kb = configs.get(kb_id)
            sid = (kb or {}).get("summary_model_id")
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
    rec = ModelStore().get_default_model("KnowledgeQA")
    return rec["id"] if rec else None

@sessions_router.get("/{session_id}/chat-images/{image_id}")
async def get_chat_image(session_id: str, image_id: str) -> Response:
    """返回会话聊天图片（MinIO 代理）。"""
    sid = _to_uuid(session_id)
    if await asyncio.to_thread(_db_get_session, sid) is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        data, content_type = await asyncio.to_thread(load_chat_image_bytes, str(sid), image_id)
    except ChatImageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type=content_type)

@sessions_router.post("/{session_id}/runs/stream")
async def stream_session_run(session_id: str, body: SessionStreamRequest) -> StreamingResponse:
    """StreamSessionRun（SSE）"""
    agent = get_agent()
    sid = _to_uuid(session_id)
    sid_str = str(sid)
    attachment_ids = normalize_attachment_ids(body.attachment_ids)
    query_text = (body.message or "").strip()

    if not attachment_ids and not query_text and not body.images and not body.input:
        raise HTTPException(status_code=400, detail="message、attachment_ids 或 images 不能为空")

    if attachment_ids and not settings.chat_images_enabled:
        raise HTTPException(status_code=400, detail="附件上传未启用")

    kb_ids = await asyncio.to_thread(_db_get_session_kb_ids, sid)
    if body.kb_ids is not None:
        kb_ids = _sanitize_kb_ids(body.kb_ids)
    chat_model_id = await asyncio.to_thread(_resolve_chat_model_id, body, kb_ids)
    vlm_model_id: str | None = None
    if body.vlm_model_id and body.vlm_model_id.strip():
        vid = body.vlm_model_id.strip()
        if not ModelStore().is_vllm_model_id_valid(vid):
            raise HTTPException(status_code=400, detail=f"无效或已禁用的 VLLM 模型: {vid}")
        vlm_model_id = vid
    configurable: dict[str, Any] = {"thread_id": sid_str, "kb_ids": kb_ids}
    if chat_model_id:
        configurable["chat_model_id"] = chat_model_id
    if vlm_model_id:
        configurable["vlm_model_id"] = vlm_model_id
    config = {
        "configurable": configurable,
        "recursion_limit": settings.agent_max_steps,
    }

    async def gen():
        expander: CitationStreamExpander | None = None
        merged_citations: dict[int, Citation] = {}
        try:
            if attachment_ids:
                yield _attachment_tool_call_frame()
                resolved = await resolve_attachments(
                    sid_str,
                    attachment_ids,
                    query_text or "请根据附件回答",
                )
                yield _attachment_tool_result_frame(
                    parsed=resolved.parsed_count,
                    skipped=resolved.skipped_count,
                )
                if not resolved.ready_rows:
                    detail = "附件未就绪或不存在"
                    if resolved.skipped_ids:
                        detail += f"（跳过: {', '.join(resolved.skipped_ids[:3])}）"
                    yield _sse_frame("error", {"message": detail})
                    return
                input_messages = [
                    build_human_message_with_attachments(
                        query_text or "请根据附件回答",
                        resolved.ready_rows,
                        sid_str,
                        skipped_ids=resolved.skipped_ids,
                    )
                ]
            else:
                input_messages = await _build_input_messages(body, sid)
                if not input_messages:
                    yield _sse_frame("error", {"message": "message、attachment_ids 或 images 不能为空"})
                    return

            async for mode, payload in agent.astream(
                {"messages": input_messages},
                config,
                stream_mode=["messages", "custom"],
            ):
                if mode == "custom":
                    if isinstance(payload, dict) and payload.get("type") in (
                        "thinking",
                        "tool_call",
                        "tool_result",
                    ):
                        yield _sse_frame("messages", payload)
                    elif isinstance(payload, dict) and payload.get("type") == "citation_meta":
                        if settings.citation_enabled:
                            batch = _citations_from_payload(payload)
                            merged_citations = merge_citation_maps(merged_citations, batch)
                            expander = CitationStreamExpander(merged_citations)
                        yield _sse_frame("messages", payload)
                    continue

                chunk, meta = payload
                if not isinstance(meta, dict) or meta.get("langgraph_node") != "agent":
                    continue
                if getattr(chunk, "tool_call_chunks", None):
                    continue
                text = _chunk_text(chunk)
                if not text:
                    continue
                if expander is not None:
                    text = expander.feed(text)
                if text:
                    yield _sse_frame("messages", {"type": "answer", "content": text})
            if expander is not None:
                rest = expander.flush()
                if rest:
                    yield _sse_frame("messages", {"type": "answer", "content": rest})
                if expander.dropped_count:
                    logger.warning(
                        "本轮剥离 %d 个非法/越界引用句柄，实际引用 %s",
                        expander.dropped_count,
                        expander.used_indexes,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("agent 运行失败")
            yield _sse_frame("error", {"message": str(e)})
        finally:
            try:
                await asyncio.to_thread(_db_touch_session, sid)
            except Exception as e:
                logger.warning("刷新会话 updated_at 失败: %s", e)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
